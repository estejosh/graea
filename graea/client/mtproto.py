"""TelethonTransport: TransportProtocol backed by a real Telegram MTProto session.

Public API: `TelethonTransport(settings: Settings)`, implementing
`graea.client.protocol.TransportProtocol`, plus `login_interactive` for
first-time auth (used by the CLI's `graea login`).

Design notes for the engine author
-----------------------------------
* `press_inline` takes an `ObservedMessage` (per the protocol), not a
  telethon message. Internally it looks the *live* telethon message up by
  `message.message_id` in `self._tl_cache` (populated by `open_chat`'s
  initial history fetch and by every event handler as messages arrive) and
  calls `.click(...)` on that. If the id isn't cached (e.g. you're holding an
  ObservedMessage from a previous process, or the message scrolled out of
  telethon's own internal cache) it falls back to `get_messages(chat, ids=...)`
  to fetch it fresh. Either way you never need to hand telethon objects
  around yourself — ObservedMessage + message_id is enough.
* Telethon message objects are never returned from this module — everything
  crossing the TransportProtocol boundary is a pydantic model from
  `graea.models`, built via `graea.client.serialize.message_to_observed`.
* `self.last_error` is set whenever a callback press could not be confirmed
  (bot never answered / telethon raised) so a `dead_button` style bug is
  observable without an exception; `press_inline` still returns `None` in
  that case rather than raising, matching the `dead_button` demo bug's usual
  behavior (spinner, no answer).
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from telethon import TelegramClient, events
from telethon.errors import RPCError
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message as TLMessage

from graea.client.serialize import message_to_observed
from graea.config import Settings
from graea.models import ObservationEvent, ObservedMessage


class TelethonTransport:
    """TransportProtocol implementation backed by telethon (MTProto)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_dirs()
        session: Any = StringSession(settings.session_string) if settings.session_string else str(settings.session)
        self.client = TelegramClient(session, settings.api_id, settings.api_hash)
        self.bot_id: Optional[int] = None
        self.chat_id: Optional[int] = None
        self.bot_username: Optional[str] = None
        self.last_error: Optional[str] = None

        self._tl_cache: dict[int, TLMessage] = {}  # message_id -> live telethon message
        self._queue: "asyncio.Queue[ObservationEvent]" = asyncio.Queue()
        self._handlers_registered = False
        self._handlers: list[tuple[Callable, Any]] = []

    # -- connection lifecycle -------------------------------------------------

    async def connect(self) -> None:
        """Connects to Telegram. Raises RuntimeError with login instructions
        if the session isn't authorized yet — never silently proceeds
        unauthenticated."""
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError(
                "Graea's Telegram session is not authorized. Run `graea login` "
                f"(session file: {self.settings.session}) and follow the phone-code / "
                "2FA prompts, then retry."
            )

    async def disconnect(self) -> None:
        """Disconnects the telethon client."""
        await self.client.disconnect()

    async def is_connected(self) -> bool:
        """True if the underlying telethon client is connected."""
        return self.client.is_connected()

    async def me(self) -> Optional[str]:
        """Returns '@username' (or the phone number, or None) of the logged-in test user."""
        try:
            u = await self.client.get_me()
        except Exception:
            return None
        if u is None:
            return None
        if getattr(u, "username", None):
            return f"@{u.username}"
        return getattr(u, "phone", None)

    # -- login (first-time auth; used by `graea login`) ---------------------

    async def login_interactive(
        self,
        code_callback: Optional[Callable[[], str]] = None,
        password_callback: Optional[Callable[[], str]] = None,
    ) -> str:
        """Runs telethon's interactive phone-code (+ optional 2FA) login flow
        against `settings.phone`, using the given callbacks to obtain the
        code / 2FA password (defaults to `input()` / `getpass()` if omitted,
        matching telethon's own CLI-friendly defaults). Returns the logged-in
        user's display string (see `me()`), for the CLI to print.
        """
        if not self.settings.phone:
            raise RuntimeError("GRAEA_PHONE is not set; required for login_interactive.")
        await self.client.connect()
        kwargs: dict[str, Any] = {"phone": self.settings.phone}
        if code_callback is not None:
            kwargs["code_callback"] = code_callback
        if password_callback is not None:
            kwargs["password"] = password_callback
        await self.client.start(**kwargs)
        name = await self.me()
        return name or "logged in"

    # -- chat setup + event handlers -------------------------------------------

    async def open_chat(self, bot_username: str) -> int:
        """Resolves the bot, remembers its id/chat id, seeds the telethon
        message cache from recent history, and registers event handlers
        (new/edit/delete) scoped to this chat. Returns the chat_id."""
        entity = await self.client.get_entity(bot_username)
        self.bot_username = bot_username
        self.bot_id = entity.id
        self.chat_id = entity.id

        # seed cache with recent history so press_inline works immediately
        async for msg in self.client.iter_messages(entity, limit=20):
            self._tl_cache[msg.id] = msg

        # (re)register handlers scoped to this chat; drop any previous bot's
        for h, ev in self._handlers:
            self.client.remove_event_handler(h, ev)
        self._handlers = []
        await self.drain_events()
        self._register_handlers(entity)
        self._handlers_registered = True

        return self.chat_id

    def _register_handlers(self, entity: Any) -> None:
        client = self.client
        bot_id = self.bot_id
        ev_new = events.NewMessage(chats=entity, incoming=True)
        ev_edit = events.MessageEdited(chats=entity, incoming=True)
        ev_del = events.MessageDeleted(chats=entity)

        async def _on_new(event: events.NewMessage.Event) -> None:
            self._tl_cache[event.message.id] = event.message
            observed = message_to_observed(event.message, bot_id)
            await self._queue.put(ObservationEvent(kind="new", message=observed))

        async def _on_edit(event: events.MessageEdited.Event) -> None:
            self._tl_cache[event.message.id] = event.message
            observed = message_to_observed(event.message, bot_id)
            await self._queue.put(ObservationEvent(kind="edit", message=observed))

        async def _on_delete(event: events.MessageDeleted.Event) -> None:
            ids = list(event.deleted_ids)
            for mid in ids:
                self._tl_cache.pop(mid, None)
            await self._queue.put(ObservationEvent(kind="delete", message_ids=ids))

        for handler, ev in ((_on_new, ev_new), (_on_edit, ev_edit), (_on_delete, ev_del)):
            client.add_event_handler(handler, ev)
            self._handlers.append((handler, ev))

    # -- outgoing actions -------------------------------------------------

    async def send_text(self, text: str) -> ObservedMessage:
        """Sends a plain text message (also used for commands like '/start')."""
        msg = await self.client.send_message(self.chat_id, text)
        self._tl_cache[msg.id] = msg
        return message_to_observed(msg, self.bot_id)

    async def send_file(self, path: str, caption: Optional[str] = None) -> ObservedMessage:
        """Sends a local file, with optional caption."""
        msg = await self.client.send_file(self.chat_id, path, caption=caption)
        if isinstance(msg, list):
            msg = msg[0]
        self._tl_cache[msg.id] = msg
        return message_to_observed(msg, self.bot_id)

    async def _resolve_tl_message(self, message: ObservedMessage) -> TLMessage:
        cached = self._tl_cache.get(message.message_id)
        if cached is not None:
            return cached
        fetched = await self.client.get_messages(self.chat_id, ids=message.message_id)
        if fetched is None:
            raise ValueError(
                f"message {message.message_id} not found in chat {self.chat_id}; "
                "it may have been deleted."
            )
        self._tl_cache[fetched.id] = fetched
        return fetched

    async def press_inline(
        self,
        message: ObservedMessage,
        *,
        text: Optional[str] = None,
        index: Optional[int] = None,
        row: Optional[int] = None,
        col: Optional[int] = None,
    ) -> Optional[str]:
        """Presses an inline button on `message` (resolved to its live
        telethon message by message_id — see module docstring) by text
        (case-insensitive exact, then substring), flat index, or row/col.

        Returns the callback answer text/alert if the bot answered one, or
        None if it answered with nothing / never answered (a real Telegram
        callback query can go unanswered — that's the `dead_button` bug this
        exists to catch). Sets `self.last_error` in the no-answer case so the
        caller can tell "no answer" apart from "answered with nothing" if it
        matters, without this method raising for what is a legitimate (if
        buggy) bot behavior.

        Raises ValueError if no button on the keyboard matches the selector.
        """
        self.last_error = None
        tl_msg = await self._resolve_tl_message(message)

        # Resolve the button ourselves (case-insensitive exact, then substring)
        # against the serialized keyboard, then click by row/col so telethon's
        # exact-text matching never gets in the way.
        kb = message.keyboard
        if kb is None or not kb.rows:
            raise ValueError(f"message {message.message_id} has no keyboard")
        target = None
        if row is not None and col is not None:
            try:
                target = kb.rows[row][col]
            except IndexError:
                raise ValueError(f"no button at row={row} col={col}; shape={kb.shape}")
        elif index is not None:
            flat = kb.flat()
            if index < 0 or index >= len(flat):
                raise ValueError(f"button index {index} out of range (0..{len(flat) - 1})")
            target = flat[index]
        elif text is not None:
            want = text.strip().lower()
            flat = kb.flat()
            target = next((b for b in flat if b.text.strip().lower() == want), None)
            if target is None:
                target = next((b for b in flat if want in b.text.lower()), None)
            if target is None:
                raise ValueError(
                    f"no button matching {text!r}; buttons: {[b.text for b in flat]}"
                )
        else:
            raise ValueError("press_inline requires one of text=, index=, or row=+col=")

        if target.kind == "url":
            self.last_error = "url button: nothing to press"
            return f"url:{target.data}"

        try:
            answer = await tl_msg.click(i=target.row, j=target.col)
        except RPCError as exc:
            # BotResponseTimeoutError = the bot never answered the callback
            # (dead_button). Any other RPC error is also a bot-side symptom.
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        except TypeError as exc:
            raise ValueError(str(exc)) from exc

        if answer is None:
            return None
        # BotCallbackAnswer: .message (toast text) and/or .url; alerts still
        # surface through .message with show_alert set on the underlying obj.
        detail = getattr(answer, "message", None)
        return detail

    async def press_reply_button(self, text: str) -> ObservedMessage:
        """Reply keyboards are plain text sends; just send_text under the hood."""
        return await self.send_text(text)

    # -- observing ----------------------------------------------------------

    async def drain_events(self) -> list[ObservationEvent]:
        """Returns and clears all events collected on the internal queue so far."""
        events_out: list[ObservationEvent] = []
        while not self._queue.empty():
            events_out.append(self._queue.get_nowait())
        return events_out

    async def wait_for_events(self, timeout_ms: int, quiet_ms: int) -> list[ObservationEvent]:
        """Waits up to timeout_ms for at least one event; once one arrives,
        keeps collecting until quiet_ms pass with nothing new. Returns []
        on a full timeout with no events at all."""
        collected: list[ObservationEvent] = []
        try:
            first = await asyncio.wait_for(self._queue.get(), timeout=timeout_ms / 1000)
            collected.append(first)
        except asyncio.TimeoutError:
            return []

        while True:
            try:
                ev = await asyncio.wait_for(self._queue.get(), timeout=quiet_ms / 1000)
                collected.append(ev)
            except asyncio.TimeoutError:
                break
        return collected

    async def last_bot_messages(self, n: int = 5) -> list[ObservedMessage]:
        """Fetches the last n messages from the bot chat (oldest first)."""
        msgs = await self.client.get_messages(self.chat_id, limit=n)
        msgs = list(reversed(list(msgs)))  # telethon returns newest-first
        for m in msgs:
            self._tl_cache[m.id] = m
        return [message_to_observed(m, self.bot_id) for m in msgs]

    async def chat_history_ids(self, n: int = 20) -> list[int]:
        """Returns the last n message ids in the bot chat (oldest first)."""
        msgs = await self.client.get_messages(self.chat_id, limit=n)
        return list(reversed([m.id for m in msgs]))
