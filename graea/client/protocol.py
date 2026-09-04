"""What the engine needs from a Telegram transport. Real: TelethonTransport. Tests: FakeTransport."""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from graea.models import ObservationEvent, ObservedMessage


@runtime_checkable
class TransportProtocol(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def is_connected(self) -> bool: ...
    async def me(self) -> Optional[str]: ...
    """Username or phone of the test user, for health."""

    async def open_chat(self, bot_username: str) -> int:
        """Resolve the bot, start collecting events for its chat, return chat_id."""

    async def send_text(self, text: str) -> ObservedMessage: ...
    async def send_file(self, path: str, caption: Optional[str] = None) -> ObservedMessage: ...

    async def press_inline(self, message: ObservedMessage, *, text: Optional[str] = None,
                           index: Optional[int] = None, row: Optional[int] = None,
                           col: Optional[int] = None) -> Optional[str]:
        """Press an inline button; return callback answer text (if any). Raise ValueError if no match."""

    async def press_reply_button(self, text: str) -> ObservedMessage:
        """Reply keyboards are plain text sends; kept separate for clarity."""

    async def drain_events(self) -> list[ObservationEvent]:
        """Return and clear all events collected since last drain (new/edit/delete from the bot chat)."""

    async def wait_for_events(self, timeout_ms: int, quiet_ms: int) -> list[ObservationEvent]:
        """Wait up to timeout_ms for at least one event; once one arrives keep collecting
        until quiet_ms pass with nothing new. Returns [] on timeout."""

    async def last_bot_messages(self, n: int = 5) -> list[ObservedMessage]: ...
