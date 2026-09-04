"""Shared data contracts for Graea.

Every module (client, visual, engine, interfaces, store) speaks these models.
Change them here, never ad hoc elsewhere.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# What the structural eye sees
# --------------------------------------------------------------------------


class Button(BaseModel):
    text: str
    kind: Literal["callback", "url", "switch_inline", "reply", "request_contact",
                  "request_location", "web_app", "other"] = "callback"
    data: Optional[str] = None  # callback data (decoded utf-8 if possible) or url
    row: int = 0
    col: int = 0


class Keyboard(BaseModel):
    kind: Literal["inline", "reply"]
    rows: list[list[Button]] = Field(default_factory=list)
    resize: Optional[bool] = None
    one_time: Optional[bool] = None

    @property
    def shape(self) -> list[int]:
        return [len(r) for r in self.rows]

    def flat(self) -> list[Button]:
        return [b for r in self.rows for b in r]


class MediaInfo(BaseModel):
    kind: Literal["photo", "document", "video", "audio", "voice", "sticker",
                  "animation", "poll", "location", "contact", "other"]
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_s: Optional[float] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ObservedMessage(BaseModel):
    """A message as received by the test user, fully serialized."""
    message_id: int
    chat_id: int
    from_bot: bool
    sender_id: Optional[int] = None
    date: datetime
    edit_date: Optional[datetime] = None
    text: str = ""                     # raw text, entities applied = rendered_text
    rendered_text: str = ""            # what a human reads (entities stripped/applied)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    keyboard: Optional[Keyboard] = None
    media: Optional[MediaInfo] = None
    caption: Optional[str] = None
    reply_to_message_id: Optional[int] = None
    raw: dict[str, Any] = Field(default_factory=dict)  # trimmed telethon dict


class ObservationEvent(BaseModel):
    kind: Literal["new", "edit", "delete", "callback_answer", "typing"]
    occurred_at: datetime = Field(default_factory=utcnow)
    message: Optional[ObservedMessage] = None
    message_ids: list[int] = Field(default_factory=list)  # for delete
    detail: Optional[str] = None  # e.g. callback answer text / alert


# --------------------------------------------------------------------------
# What the visual eye sees
# --------------------------------------------------------------------------


class Screenshot(BaseModel):
    path: str
    sha256: str
    width: int
    height: int
    taken_at: datetime = Field(default_factory=utcnow)
    region: Literal["chat", "last_messages", "full"] = "chat"


class VisionFinding(BaseModel):
    severity: Literal["info", "low", "medium", "high"] = "medium"
    kind: Literal["raw_markdown", "truncated_button", "missing_caption",
                  "empty_message", "broken_media", "layout", "mismatch",
                  "loading", "error_banner", "other"] = "other"
    detail: str


class VisionSeenMessage(BaseModel):
    sender: Literal["bot", "user", "unknown"] = "unknown"
    text_as_rendered: str = ""
    buttons: list[str] = Field(default_factory=list)
    media: Optional[str] = None


class VisionReading(BaseModel):
    provider: str
    model: Optional[str] = None
    description: str = ""
    messages_seen: list[VisionSeenMessage] = Field(default_factory=list)
    issues: list[VisionFinding] = Field(default_factory=list)
    ocr_text: Optional[str] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None  # set when the reader could not run; never silent

    @property
    def available(self) -> bool:
        return self.error is None and self.provider not in ("none",)


# --------------------------------------------------------------------------
# What the LLM does
# --------------------------------------------------------------------------


class ActionKind(str, Enum):
    send_text = "send_text"
    send_command = "send_command"
    press_button = "press_button"
    send_file = "send_file"
    wait = "wait"
    look = "look"


class Action(BaseModel):
    kind: ActionKind
    text: Optional[str] = None
    button_text: Optional[str] = None
    button_index: Optional[int] = None
    button_row: Optional[int] = None
    button_col: Optional[int] = None
    target_message_id: Optional[int] = None  # which message's keyboard; default last bot msg
    file_path: Optional[str] = None
    caption: Optional[str] = None
    timeout_ms: Optional[int] = None
    last_n: int = 5


# --------------------------------------------------------------------------
# Assertions
# --------------------------------------------------------------------------


AssertionKind = Literal[
    "text_contains", "text_equals", "text_regex",
    "reply_count", "no_reply", "reply_within_ms",
    "has_inline_button", "keyboard_shape", "has_reply_keyboard",
    "message_edited", "message_deleted",
    "markdown_rendered", "media_type", "caption_contains",
    "vision_no_issues", "vision_sees",
]


class AssertionSpec(BaseModel):
    kind: AssertionKind
    name: Optional[str] = None
    value: Any = None        # main argument (string, int, list, regex...)
    op: Optional[str] = None  # for reply_count: "==", ">=", "<="
    severity: Optional[str] = None  # for vision_no_issues: max tolerated severity
    case_sensitive: bool = False

    def label(self) -> str:
        return self.name or f"{self.kind}:{self.value!r}"


class AssertionResult(BaseModel):
    name: str
    kind: str
    passed: bool
    actual: Optional[str] = None
    message: str = ""


# --------------------------------------------------------------------------
# Step results and diffs — what every interface returns
# --------------------------------------------------------------------------


class StepDiff(BaseModel):
    """This step vs the same-named step in the previous run of the scenario."""
    previous_run_id: Optional[str] = None
    previous_step_id: Optional[str] = None
    fixed: list[str] = Field(default_factory=list)       # assertion names fail->pass
    regressed: list[str] = Field(default_factory=list)   # pass->fail
    still_failing: list[str] = Field(default_factory=list)
    still_passing: list[str] = Field(default_factory=list)
    new_assertions: list[str] = Field(default_factory=list)
    text_changed: bool = False
    keyboard_changed: bool = False
    vision_issue_delta: int = 0  # issues now - issues before
    summary: str = ""


class StepResult(BaseModel):
    run_id: str
    step_id: str
    idx: int
    name: str
    action: Action
    sent_at: datetime
    done_at: datetime
    timed_out: bool = False
    events: list[ObservationEvent] = Field(default_factory=list)
    replies: list[ObservedMessage] = Field(default_factory=list)   # new bot messages this step
    edits: list[ObservedMessage] = Field(default_factory=list)
    deleted_ids: list[int] = Field(default_factory=list)
    screenshot: Optional[Screenshot] = None
    screenshot_error: Optional[str] = None
    vision: Optional[VisionReading] = None
    assertions: list[AssertionResult] = Field(default_factory=list)
    diff: Optional[StepDiff] = None
    progress: str = ""   # one line for the LLM: "2 fixed, 1 regressed, 4 still failing since run 3"
    notes: list[str] = Field(default_factory=list)  # anything the engine wants the LLM to know

    @property
    def passed(self) -> bool:
        return all(a.passed for a in self.assertions)


class RunSummary(BaseModel):
    run_id: str
    scenario: str
    bot_username: Optional[str] = None
    fingerprint: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: Literal["running", "passed", "failed", "aborted"] = "running"
    steps: int = 0
    assertions_total: int = 0
    assertions_passed: int = 0
    previous_run_id: Optional[str] = None
    progress: str = ""
    step_results: list[StepResult] = Field(default_factory=list)
    notes: Optional[str] = None


class DiffReport(BaseModel):
    scenario: str
    run_a: str
    run_b: str
    fingerprint_a: Optional[str] = None
    fingerprint_b: Optional[str] = None
    fixed: list[str] = Field(default_factory=list)
    regressed: list[str] = Field(default_factory=list)
    still_failing: list[str] = Field(default_factory=list)
    still_passing: list[str] = Field(default_factory=list)
    new_assertions: list[str] = Field(default_factory=list)
    missing_steps: list[str] = Field(default_factory=list)
    summary: str = ""


# --------------------------------------------------------------------------
# Scenarios (YAML)
# --------------------------------------------------------------------------


class ScenarioStep(BaseModel):
    name: str
    action: Action
    expect: list[AssertionSpec] = Field(default_factory=list)
    timeout_ms: Optional[int] = None


class Scenario(BaseModel):
    name: str
    description: str = ""
    bot: Optional[str] = None
    source_path: Optional[str] = None
    steps: list[ScenarioStep]


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


class Health(BaseModel):
    mtproto_connected: bool = False
    mtproto_user: Optional[str] = None
    web_logged_in: Optional[bool] = None
    web_error: Optional[str] = None
    vision_provider: str = "none"
    vision_model: Optional[str] = None
    vision_ok: Optional[bool] = None
    ocr_available: bool = False
    db_path: str = ""
    bot: Optional[str] = None
    active_run_id: Optional[str] = None
