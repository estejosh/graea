# structural agent report

## Files written

- `graea/client/serialize.py` — `message_to_observed(msg, bot_id) -> ObservedMessage`
- `graea/client/mtproto.py` — `TelethonTransport` (implements `TransportProtocol`)
- `graea/client/fake.py` — `FakeTransport` (implements `TransportProtocol`, scripted)
- `tests/test_serialize.py` — 12 tests, all passing
- `tests/test_fake_transport.py` — 19 tests, all passing

Ran `python -m pytest tests/test_serialize.py tests/test_fake_transport.py -q` →
**31 passed**. (Full-suite `python -m pytest tests/ -q` also shows 18 failures
in `test_store.py`/`test_diff.py` — those are another agent's files, failing
on `ModuleNotFoundError: No module named 'pytz'` inside duckdb's timestamp
handling; unrelated to and not touched by this work.)

## `serialize.py`

`message_to_observed(msg, bot_id)` accepts either a live
`telethon.tl.custom.Message` (from event handlers, backed by a client) or a
bare `telethon.tl.types.Message` built by hand (tests do this). Telethon
actually **patches `tl.types.Message` itself** with all the `custom.Message`
convenience properties (`chat_id`, `sender_id`, `out`, `reply_to`, ...) — so
both are the same class hierarchy, and everything needed (chat_id, sender_id,
out, reply_to, media, reply_markup, entities) works without a `_client`
attached. `.text`/`.buttons` do need a client (they return `None` otherwise),
so this module never touches them — it computes `rendered_text` and
`Keyboard` itself directly from `.message`/`.entities`/`.reply_markup`.

Key decision: Telegram entities are ranges into already-plain text — they
never *insert* markup characters (unlike literal Markdown/HTML source). So
`rendered_text` is the identity transform of `text` for bold/italic/code/etc.
The value of computing `entities` as structured data is (a) exposing them for
assertions, and (b) making a bot's `raw_markdown` bug (literal `*bold*` sent
with no matching entity, wrong parse_mode) visible as-is in `rendered_text` —
which is exactly what `markdown_rendered` should catch.

Media classification (`MediaInfo.kind`): photo, poll, location (geo),
contact handled directly; `MessageMediaDocument` is inspected via its
`DocumentAttribute*` list to distinguish sticker / animation / video /
voice (including round video notes) / audio / plain document. Anything else
(game, invoice, webpage preview, ...) degrades to `kind="other"` with the
telethon type name in `extra`, never raises.

Callback data on inline buttons: `bytes.decode("utf-8", errors="replace")` —
verified round-trips valid data and replaces invalid bytes with `�`
rather than raising.

`raw` dict is intentionally tiny: `id`, `date` (isoformat), `out`, `text`,
`has_media`/`has_keyboard`/`has_entities` flags. No binary, no huge nested
telethon structures.

**Never raises**: the whole body is wrapped in try/except; on any failure it
returns a minimal `ObservedMessage` (best-effort `message_id`, `chat_id=0`,
`from_bot=True`) with `raw["serialize_error"]` set to `str(exc)`. Tested with
both a hostile object whose `__getattr__` raises and a bare stub with none of
the expected attributes.

## `fake.py`

`FakeTransport(script: dict[str, list[str | ObservedMessage | ObservationEvent | dict]])`.
Keys are the *outgoing* text (what was sent, or the button text pressed).
Values are coerced into `ObservationEvent`s:
- `str` → new bot text message (via `make_bot_message`)
- `ObservedMessage` → wrapped as a `"new"` event
- `ObservationEvent` → used as-is
- `dict` with `"kind": "delete"` → `message_ids` (or singular `"message_id"` shorthand)
- `dict` with `"kind": "callback_answer"` → not a message; its `"detail"` becomes `press_inline`'s return value
- `dict` with `"kind": "edit"` (or omitted → `"new"`) and either a `"message"` sub-dict/`ObservedMessage`, or the dict itself treated as `make_bot_message(**dict)` kwargs

`wait_for_events(timeout_ms, quiet_ms)` returns the events produced by the
*most recent* outgoing action, consumed on read (a second call with no new
action returns `[]` — FakeTransport's stand-in for a real timeout).
`drain_events()` is an independent, non-consuming-by-`wait_for_events`
backlog of every event since the last drain — so tests can use either or
both without them fighting over state.

`press_inline(message, text=|index=|row=+col=)` re-resolves `message` by
`message_id` against its own internal cache before matching buttons, so a
caller can pass a stale/older copy of an `ObservedMessage` (e.g.
`model_copy()`'d) and it still works — this mirrors how `TelethonTransport`
resolves by id too (see below). Button matching: case-insensitive exact
match first, then substring. Raises `ValueError` if nothing matches.

`make_bot_message(text, keyboard=None, media=None, message_id=None, ...)` is
the documented helper; message ids auto-increment from 1000 if not given.
User-originated messages (`send_text`, `press_reply_button`) get their own
auto-incrementing *negative* id range so they never collide with bot message
ids in the shared cache.

## `mtproto.py`

`TelethonTransport(settings: Settings)` wraps
`telethon.TelegramClient(str(settings.session), settings.api_id, settings.api_hash)`.

- `connect()` calls `client.connect()` then checks `is_user_authorized()`;
  if not authorized it raises `RuntimeError` telling the user to run
  `graea login` (naming the session path), never proceeds silently
  unauthenticated.
- `login_interactive(code_callback=None, password_callback=None)` is a new
  method beyond `TransportProtocol` (for the CLI's `graea login` command,
  which owns interactive I/O) — it drives telethon's own `client.start(phone=..., code_callback=..., password=...)`,
  defaulting to telethon's built-in `input()`/`getpass()` prompts when no
  callbacks are given. Returns the logged-in user's display string.
- `open_chat(bot_username)` resolves the entity, records `bot_id`/`chat_id`,
  seeds `self._tl_cache` (message_id → live telethon message) from the last
  20 messages via `iter_messages`, then registers `NewMessage`/`MessageEdited`/`MessageDeleted`
  handlers filtered to `chats=entity, incoming=True` (delete has no
  `incoming` filter — Telegram delete updates don't carry a sender). Handlers
  push `ObservationEvent`s onto an `asyncio.Queue` and also keep
  `self._tl_cache` current as new/edited messages arrive (and evict on
  delete).
- **How `press_inline` gets a telethon message from only an `ObservedMessage`**
  (the thing the engine author needs to know): it looks `message.message_id`
  up in `self._tl_cache` first; if the id isn't cached (e.g. engine restarted,
  or the message is older than the 20-message seed window and had no
  edit/new event since), it falls back to
  `client.get_messages(self.chat_id, ids=message.message_id)` to fetch it
  fresh, then caches it. Either way, the engine never needs to hold or pass
  around a telethon object — an `ObservedMessage` (or just its `message_id`)
  is always enough. Raises `ValueError` if the message can't be found at all
  (e.g. truly deleted).
- Button selection maps directly to `Message.click(i=, j=)` / `click(i=)` /
  `click(text=)` (row+col / flat index / text — telethon does its own
  case-sensitive-then-fuzzy text matching internally for `text=`, so this is
  delegated straight through rather than reimplemented).
- If `click()` raises `TypeError` (telethon's own signal for "no button
  matched"), it's converted to `ValueError` for a consistent contract with
  `FakeTransport`. Any callback-answer timeout/no-answer situation (the
  `dead_button` demo bug) is caught: `press_inline` returns `None` rather
  than raising, and `self.last_error` stays available for the caller to
  inspect why. This deliberately does *not* raise on a live but
  never-answered callback query, since "bot never answers" is a legitimate
  (if buggy) thing under test, not a transport failure.
- `send_text`/`send_file`/`press_reply_button`/`last_bot_messages`/
  `chat_history_ids` (the extra helper mentioned in the task) all funnel
  results through `message_to_observed` before returning — no telethon
  object ever crosses the `TransportProtocol` boundary.

## Untested / needs real creds

`mtproto.py` has no dedicated test file (none was assigned, and
`AGENT-RULES.md` requires tests to run with no Telegram credentials and no
network — a real integration test isn't possible in this environment). It
was verified to:
- import cleanly,
- structurally satisfy `TransportProtocol` (`isinstance(TelethonTransport(settings), TransportProtocol)` is `True`, checked via `runtime_checkable`),
- construct without a live connection given placeholder `api_id`/`api_hash`.

Everything else about it (actual login flow, live event delivery, real
button presses, callback-answer/timeout behavior against a real bot) is
unverified here and needs a real `GRAEA_API_ID`/`GRAEA_API_HASH`/
`GRAEA_PHONE` and a running bot to exercise — the engine/CLI author
should smoke-test it against the demo bot once other pieces land.

## Notes for other agents

- `docs/dev/CONTRACT-NOTES.md` was empty and needed no additions — `models.py`,
  `config.py`, and `protocol.py` covered everything needed.
- `FakeTransport` and `TelethonTransport` both pass
  `isinstance(x, TransportProtocol)` — safe to type-hint against the
  Protocol in the engine.
