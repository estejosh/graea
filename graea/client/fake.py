"""FakeTransport: a scripted TransportProtocol implementation.

For tests, and for anyone exercising an LLM loop against Graea without a
real Telegram account. No network, no telethon client involved.

Usage
-----
    transport = FakeTransport(script={
        "/start": ["Welcome!"],
        "OK": [{"kind": "callback_answer", "detail": "done"}],
    })
    await transport.connect()
    await transport.open_chat("@some_bot")
    await transport.send_text("/start")
    events = await transport.wait_for_events(timeout_ms=1000, quiet_ms=100)
    # events == [ObservationEvent(kind="new", message=ObservedMessage(text="Welcome!", ...))]

The script's keys are the *outgoing* text: whatever was sent via
`send_text`/`press_reply_button`, or the *button text* pressed via
`press_inline`. `send_file` looks its key up by caption first, then by the
file path, then by the literal string `"file:<path>"`.

Each value is a list of "what the bot does in response" — any mix of:
  - a plain `str`               -> a new bot text message
  - an `ObservedMessage`        -> wrapped as a "new" event
  - an `ObservationEvent`       -> used as-is
  - a `dict`                    -> either a full event shape
                                    (`{"kind": "edit"|"delete"|"new"|"callback_answer",
                                       "message": {...} | ObservedMessage, "message_ids": [...],
                                       "detail": "..."}`)
                                    or, with no "kind" key, treated as kwargs to
                                    `make_bot_message(**dict)` for a "new" event.

`wait_for_events` returns exactly the events scripted for the most recent
outgoing action (building fresh `ObservedMessage`s / ids each time it fires),
or `[]` if that action had no script entry — this is FakeTransport's stand-in
for a real timeout. `drain_events` independently accumulates every event
produced by every action since the last drain, so it stays useful even if the
caller never calls `wait_for_events`.
"""
from __future__ import annotations

from typing import Any, Optional, Union

from graea.models import (
    Button,
    Keyboard,
    MediaInfo,
    ObservationEvent,
    ObservedMessage,
    utcnow,
)

ScriptEntry = Union[str, ObservedMessage, ObservationEvent, dict]


class FakeTransport:
    """Scripted stand-in for TelethonTransport. Implements TransportProtocol."""

    def __init__(self, script: Optional[dict[str, list[ScriptEntry]]] = None):
        self.script: dict[str, list[ScriptEntry]] = dict(script or {})
        self._connected = False
        self.me_name = "fake_test_user"
        self.bot_id = -1001  # sentinel "bot" sender id used for scripted messages
        self.chat_id = 1
        self.bot_username: Optional[str] = None
        self.last_error: Optional[str] = None

        self._messages: dict[int, ObservedMessage] = {}
        self._bot_order: list[int] = []  # message_ids of bot messages, in creation order
        self._pending: list[ObservationEvent] = []  # accumulated since last drain_events
        self._last_events: list[ObservationEvent] = []  # produced by the most recent action
        self._next_bot_id = 1000
        self._next_user_id = 1

    # -- connection lifecycle ------------------------------------------------

    async def connect(self) -> None:
        """Marks the fake transport connected. Never fails."""
        self._connected = True

    async def disconnect(self) -> None:
        """Marks the fake transport disconnected."""
        self._connected = False

    async def is_connected(self) -> bool:
        """True once connect() has been called (and disconnect() has not)."""
        return self._connected

    async def me(self) -> Optional[str]:
        """Returns the fake test-user's display name, for health checks."""
        return self.me_name

    async def open_chat(self, bot_username: str) -> int:
        """Records the target bot and returns a fixed fake chat_id (1)."""
        self.bot_username = bot_username
        return self.chat_id

    # -- message construction -------------------------------------------------

    def make_bot_message(
        self,
        text: str = "",
        keyboard: Optional[Keyboard] = None,
        media: Optional[MediaInfo] = None,
        message_id: Optional[int] = None,
        caption: Optional[str] = None,
        entities: Optional[list[dict[str, Any]]] = None,
        edit_date=None,
        reply_to_message_id: Optional[int] = None,
    ) -> ObservedMessage:
        """Builds (and registers) an ObservedMessage as if sent by the bot.

        Returns the ObservedMessage so callers/scripts can reference its id.
        """
        if message_id is None:
            message_id = self._next_bot_id
            self._next_bot_id += 1
        msg = ObservedMessage(
            message_id=message_id,
            chat_id=self.chat_id,
            from_bot=True,
            sender_id=self.bot_id,
            date=utcnow(),
            edit_date=edit_date,
            text=text,
            rendered_text=text,
            entities=entities or [],
            keyboard=keyboard,
            media=media,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
            raw={"id": message_id, "text": text, "fake": True},
        )
        self._register(msg)
        return msg

    def _register(self, msg: ObservedMessage) -> None:
        self._messages[msg.message_id] = msg
        if msg.from_bot and msg.message_id not in self._bot_order:
            self._bot_order.append(msg.message_id)

    def _make_user_message(self, text: str) -> ObservedMessage:
        message_id = -self._next_user_id
        self._next_user_id += 1
        msg = ObservedMessage(
            message_id=message_id,
            chat_id=self.chat_id,
            from_bot=False,
            sender_id=0,
            date=utcnow(),
            text=text,
            rendered_text=text,
            raw={"id": message_id, "text": text, "fake": True},
        )
        self._register(msg)
        return msg

    # -- script -> events -------------------------------------------------

    def _coerce_event(self, entry: ScriptEntry) -> ObservationEvent:
        if isinstance(entry, ObservationEvent):
            ev = entry
        elif isinstance(entry, ObservedMessage):
            self._register(entry)
            ev = ObservationEvent(kind="new", message=entry)
        elif isinstance(entry, str):
            ev = ObservationEvent(kind="new", message=self.make_bot_message(entry))
        elif isinstance(entry, dict):
            kind = entry.get("kind", "new")
            if kind == "delete":
                ids = entry.get("message_ids")
                if ids is None and "message_id" in entry:
                    ids = [entry["message_id"]]
                ev = ObservationEvent(kind="delete", message_ids=list(ids or []))
            elif kind == "callback_answer":
                ev = ObservationEvent(kind="callback_answer", detail=entry.get("detail"))
            else:
                msg_field = entry.get("message")
                if msg_field is not None:
                    msg = (
                        msg_field
                        if isinstance(msg_field, ObservedMessage)
                        else self.make_bot_message(**msg_field)
                    )
                    self._register(msg)
                else:
                    msg_kwargs = {k: v for k, v in entry.items() if k != "kind"}
                    msg = self.make_bot_message(**msg_kwargs)
                ev = ObservationEvent(kind=kind, message=msg, detail=entry.get("detail"))
        else:
            raise TypeError(f"Unsupported FakeTransport script entry type: {type(entry)!r}")
        return ev

    def _fire(self, key: str) -> list[ObservationEvent]:
        """Turns script[key] (if any) into fresh events; records them as
        both 'the last action's events' (for wait_for_events) and appends
        them to the drain_events backlog."""
        entries = self.script.get(key, [])
        events = [self._coerce_event(e) for e in entries]
        self._last_events = events
        self._pending.extend(events)
        return events

    # -- outgoing actions -------------------------------------------------

    async def send_text(self, text: str) -> ObservedMessage:
        """Records an outgoing text message and queues its scripted reply (if any)."""
        msg = self._make_user_message(text)
        self._fire(text)
        return msg

    async def send_file(self, path: str, caption: Optional[str] = None) -> ObservedMessage:
        """Records an outgoing file send. Script lookup key: caption, then
        path, then the literal string 'file:<path>'."""
        display_text = caption if caption else f"file:{path}"
        msg = self._make_user_message(display_text)
        if caption and caption in self.script:
            key = caption
        elif path in self.script:
            key = path
        else:
            key = f"file:{path}"
        self._fire(key)
        return msg

    def _find_button(
        self,
        message: ObservedMessage,
        *,
        text: Optional[str],
        index: Optional[int],
        row: Optional[int],
        col: Optional[int],
    ) -> Optional[Button]:
        kb = message.keyboard
        if kb is None or kb.kind != "inline":
            return None
        if row is not None and col is not None:
            try:
                return kb.rows[row][col]
            except IndexError:
                return None
        if index is not None:
            flat = kb.flat()
            if 0 <= index < len(flat):
                return flat[index]
            return None
        if text is not None:
            flat = kb.flat()
            low = text.lower()
            for b in flat:
                if b.text.lower() == low:
                    return b
            for b in flat:
                if low in b.text.lower():
                    return b
            return None
        return None

    async def press_inline(
        self,
        message: ObservedMessage,
        *,
        text: Optional[str] = None,
        index: Optional[int] = None,
        row: Optional[int] = None,
        col: Optional[int] = None,
    ) -> Optional[str]:
        """Presses a button by text (case-insensitive exact, then substring),
        flat index, or row/col on the given message's keyboard. Looks the
        message up in the internal cache by message_id first (falling back
        to the passed-in object) so callers can pass a message obtained
        earlier, matching how TelethonTransport works. Script lookup key is
        the pressed button's text. Returns the scripted callback-answer
        detail, or None if no `callback_answer` entry was scripted.

        Raises ValueError if no button matches the given selector.
        """
        stored = self._messages.get(message.message_id, message)
        btn = self._find_button(stored, text=text, index=index, row=row, col=col)
        if btn is None:
            raise ValueError(
                f"no matching inline button on message {message.message_id} "
                f"(text={text!r}, index={index!r}, row={row!r}, col={col!r})"
            )
        events = self._fire(btn.text)
        answer = next((ev.detail for ev in events if ev.kind == "callback_answer"), None)
        if answer is None and btn.text not in self.script:
            self.last_error = f"no script entry for button {btn.text!r}"
        return answer

    async def press_reply_button(self, text: str) -> ObservedMessage:
        """Reply-keyboard buttons are plain text sends; scripted like send_text."""
        msg = self._make_user_message(text)
        self._fire(text)
        return msg

    # -- observing ----------------------------------------------------------

    async def drain_events(self) -> list[ObservationEvent]:
        """Returns and clears every event produced by every action since the
        last drain_events call (independent of wait_for_events)."""
        events = self._pending
        self._pending = []
        return events

    async def wait_for_events(self, timeout_ms: int, quiet_ms: int) -> list[ObservationEvent]:
        """Returns the events scripted for the most recent outgoing action,
        or [] if that action had no script entry (FakeTransport's stand-in
        for a real timeout). Consumes those events from the drain_events
        backlog too, so a later drain_events() doesn't see them again."""
        events = self._last_events
        self._last_events = []
        if events:
            remaining = list(self._pending)
            for ev in events:
                if ev in remaining:
                    remaining.remove(ev)
            self._pending = remaining
        return events

    async def last_bot_messages(self, n: int = 5) -> list[ObservedMessage]:
        """Returns up to the last n bot messages, oldest first."""
        ids = self._bot_order[-n:]
        return [self._messages[i] for i in ids]
