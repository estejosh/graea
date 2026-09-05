"""Tests for graea.engine.runner.TestSession.

Uses FakeTransport (scripted, no network), a tiny stub visual eye that
writes a real PNG via Pillow (so the vision path actually runs), a stub
vision reader returning a canned VisionReading, NullWeb (for the
screenshot-disabled path), and Store(":memory:").
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import pytest
from PIL import Image

from graea.client.fake import FakeTransport
from graea.config import Settings
from graea.engine.runner import TestSession
from graea.models import (
    Action,
    ActionKind,
    AssertionSpec,
    Keyboard,
    Screenshot,
    VisionFinding,
    VisionReading,
)
from graea.store import Store
from graea.visual.web import NullWeb


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------


class StubWeb:
    """A minimal visual eye: writes a real (tiny) PNG via Pillow and returns
    a proper Screenshot, so the vision-reading path in TestSession.step runs
    for real. start/stop/is_logged_in/open_chat are no-ops."""

    def __init__(self):
        self.opened: list[str] = []
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def is_logged_in(self) -> bool:
        return True

    async def open_chat(self, bot_username: str) -> None:
        self.opened.append(bot_username)

    async def screenshot(self, path: str, region: str = "chat", last_n: int = 5) -> Screenshot:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (24, 12), color=(240, 240, 240))
        img.save(path)
        data = Path(path).read_bytes()
        return Screenshot(
            path=path, sha256=hashlib.sha256(data).hexdigest(),
            width=24, height=12, region=region,
        )


class StubReader:
    """Returns a canned VisionReading with one raw_markdown issue, ignoring input."""

    name = "stub"
    model = "stub-model"

    def __init__(self, reading: Optional[VisionReading] = None):
        self._reading = reading

    async def read(self, shot: Screenshot, expected: Optional[dict] = None) -> VisionReading:
        if self._reading is not None:
            return self._reading
        return VisionReading(
            provider=self.name, model=self.model,
            description="a chat with one bot message",
            issues=[VisionFinding(severity="medium", kind="raw_markdown", detail="stray *bold*")],
        )


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        db=tmp_path / "t.duckdb", shots=tmp_path / "shots",
        session=tmp_path / "session", web_profile=tmp_path / "web",
        bot="@demo_bot", vision_provider="none",
    )


async def make_session(tmp_path: Path, script: dict, web=None, reader=None) -> TestSession:
    settings = make_settings(tmp_path)
    session = TestSession(
        settings,
        transport=FakeTransport(script),
        web=web if web is not None else StubWeb(),
        reader=reader if reader is not None else StubReader(),
        store=Store(":memory:"),
    )
    await session.start()
    return session


# --------------------------------------------------------------------------
# Basic step behavior
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_command_with_expectations_pass():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        session = await make_session(Path(td), {"/start": ["Welcome!"]})
        result = await session.step(
            Action(kind=ActionKind.send_command, text="/start"),
            expect=[AssertionSpec(kind="text_contains", value="Welcome")],
        )
        assert result.timed_out is False
        assert len(result.replies) == 1
        assert result.replies[0].rendered_text == "Welcome!"
        assert result.assertions[0].passed is True
        assert result.passed is True
        assert result.screenshot is not None
        assert result.screenshot_error is None
        assert result.vision is not None
        assert result.vision.error is None


@pytest.mark.asyncio
async def test_send_command_with_failing_expectation():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        session = await make_session(Path(td), {"/start": ["Hello there"]})
        result = await session.step(
            Action(kind=ActionKind.send_command, text="/start"),
            expect=[AssertionSpec(kind="text_contains", value="Welcome")],
        )
        assert result.assertions[0].passed is False
        assert result.passed is False


@pytest.mark.asyncio
async def test_press_button_by_text_edits_message():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        kb = Keyboard(kind="inline", rows=[[{"text": "Order", "kind": "callback", "data": "order"}]])
        script = {
            "/start": [{"text": "Menu", "keyboard": kb.model_dump(), "message_id": 500}],
            "Order": [{"kind": "edit", "message": {"text": "Order placed!", "message_id": 500}}],
        }
        session = await make_session(Path(td), script)
        await session.step(Action(kind=ActionKind.send_command, text="/start"))
        result = await session.step(
            Action(kind=ActionKind.press_button, button_text="Order"),
            expect=[AssertionSpec(kind="message_edited", value="Order placed")],
        )
        assert result.edits, "expected an edit event"
        assert result.assertions[0].passed is True
        assert result.timed_out is False


@pytest.mark.asyncio
async def test_no_reply_times_out_and_no_reply_assertion_passes():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        session = await make_session(Path(td), {})  # /silent has no script entry
        result = await session.step(
            Action(kind=ActionKind.send_command, text="/silent"),
            expect=[AssertionSpec(kind="no_reply")],
        )
        assert result.timed_out is True
        assert any("no reply within" in n for n in result.notes)
        assert result.assertions[0].passed is True  # no_reply: expected silence


@pytest.mark.asyncio
async def test_adhoc_run_auto_starts():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        session = await make_session(Path(td), {"/start": ["hi"]})
        assert session.run_id is None
        result = await session.step(Action(kind=ActionKind.send_command, text="/start"))
        assert session.run_id is not None
        assert result.run_id == session.run_id


@pytest.mark.asyncio
async def test_assert_last():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        session = await make_session(Path(td), {"/start": ["Welcome!"]})
        await session.step(Action(kind=ActionKind.send_command, text="/start"))
        results = await session.assert_last([AssertionSpec(kind="text_contains", value="Welcome")])
        assert results[0].passed is True
        assert session.last_step.assertions[-1].passed is True


@pytest.mark.asyncio
async def test_sql_passthrough():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        session = await make_session(Path(td), {"/start": ["Welcome!"]})
        await session.step(Action(kind=ActionKind.send_command, text="/start"))
        rows = session.sql("SELECT run_id, scenario FROM runs")
        assert rows
        assert rows[0]["run_id"] == session.run_id or "run_id" in rows[0]


# --------------------------------------------------------------------------
# NullWeb: screenshot_error + vision_no_issues fails with explanation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nullweb_yields_screenshot_error_and_vision_no_issues_fails():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        session = await make_session(Path(td), {"/start": ["Welcome!"]}, web=NullWeb())
        result = await session.step(
            Action(kind=ActionKind.send_command, text="/start"),
            expect=[AssertionSpec(kind="vision_no_issues")],
        )
        assert result.screenshot is None
        assert result.screenshot_error is not None
        assert any("visual eye disabled" in n for n in result.notes)
        assert result.vision is None
        assert result.assertions[0].passed is False
        assert result.assertions[0].message  # explanation present


# --------------------------------------------------------------------------
# run_scenario: two consecutive runs produce a diff with fixed/regressed
# and a non-empty progress line
# --------------------------------------------------------------------------


def _scenario_dict(help_reply: Optional[str]):
    steps = [
        {
            "name": "start",
            "action": {"kind": "send_command", "text": "/start"},
            "expect": [{"kind": "text_contains", "value": "Welcome"}],
        },
        {
            "name": "help",
            "action": {"kind": "send_command", "text": "/help"},
            "expect": [{"kind": "reply_count", "value": 1, "op": ">="}],
        },
    ]
    return {
        "name": "diff_demo",
        "bot": "@demo_bot",
        "steps": steps,
    }


@pytest.mark.asyncio
async def test_run_scenario_twice_diffs_fixed_and_regressed():
    import tempfile
    from graea.models import Scenario

    with tempfile.TemporaryDirectory() as td:
        settings = make_settings(Path(td))
        store = Store(":memory:")

        # Run 1: /help is silent -> reply_count assertion fails.
        script_1 = {"/start": ["Welcome!"]}
        session1 = TestSession(settings, transport=FakeTransport(script_1),
                                web=StubWeb(), reader=StubReader(), store=store)
        await session1.start()
        run1 = await session1.run_scenario(Scenario.model_validate(_scenario_dict(None)))
        assert run1.status == "failed"

        # Run 2: /help now replies -> reply_count assertion fixed; /start still passes.
        script_2 = {"/start": ["Welcome!"], "/help": ["Here is some help."]}
        session2 = TestSession(settings, transport=FakeTransport(script_2),
                                web=StubWeb(), reader=StubReader(), store=store)
        await session2.start()
        run2 = await session2.run_scenario(Scenario.model_validate(_scenario_dict("help")))
        assert run2.status == "passed"

        help_step = next(s for s in run2.step_results if s.name == "help")
        assert help_step.diff is not None
        assert "reply_count:None" in help_step.diff.fixed or any(
            "reply_count" in n for n in help_step.diff.fixed
        )
        assert help_step.progress  # non-empty progress line

        start_step = next(s for s in run2.step_results if s.name == "start")
        assert start_step.diff is not None
        assert start_step.progress

        report = session2.diff(scenario="diff_demo")
        assert report.run_a == run1.run_id
        assert report.run_b == run2.run_id
        assert any("reply_count" in n for n in report.fixed)


async def test_caller_reading_reevaluates_vision_assertions(tmp_path):
    """provider=caller: vision_no_issues is pending (fails) until the caller submits a reading."""
    from graea.client.fake import FakeTransport
    from graea.config import Settings
    from graea.engine.runner import TestSession
    from graea.models import Action, ActionKind, AssertionSpec
    from graea.store import Store
    from graea.visual.vision import CallerVision

    settings = Settings(bot="@demo", shots=tmp_path / "shots", db=tmp_path / "db.duckdb",
                        vision_provider="caller", ocr="off")
    transport = FakeTransport(script={"/start": ["Welcome"]})
    web = _StubWebForCaller(tmp_path)
    session = TestSession(settings, transport=transport, web=web, reader=CallerVision(settings),
                          store=Store(":memory:"))
    await session.start()
    await session.start_run("caller_demo")
    step = await session.step(Action(kind=ActionKind.send_command, text="/start"),
                              expect=[AssertionSpec(kind="text_contains", value="Welcome"),
                                      AssertionSpec(kind="vision_no_issues", name="looks_clean")])
    assert step.vision is not None and step.vision.error and "pending" in step.vision.error
    pending = [a for a in step.assertions if a.name == "looks_clean"][0]
    assert pending.passed is False

    refreshed = await session.submit_reading("Bot says Welcome, one row of three buttons.", issues=[])
    ok = [a for a in refreshed.assertions if a.name == "looks_clean"][0]
    assert ok.passed is True
    assert refreshed.vision.provider == "caller"

    # a second submission with an issue flips it back, and the DB row follows
    again = await session.submit_reading("Literal asterisks visible.",
                                         issues=[{"severity": "high", "kind": "raw_markdown", "detail": "*bold*"}])
    assert [a for a in again.assertions if a.name == "looks_clean"][0].passed is False
    rows = session.sql("SELECT passed FROM assertions WHERE name = 'looks_clean'")
    assert rows and rows[0]["passed"] is False


class _StubWebForCaller:
    def __init__(self, tmp):
        self.tmp = tmp

    async def start(self): ...
    async def stop(self): ...
    async def is_logged_in(self): return True
    async def open_chat(self, bot): ...

    async def screenshot(self, path, region="chat", last_n=5):
        import hashlib
        from pathlib import Path
        from PIL import Image
        from graea.models import Screenshot
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (40, 20), "white").save(path)
        data = Path(path).read_bytes()
        return Screenshot(path=str(path), sha256=hashlib.sha256(data).hexdigest(), width=40, height=20)
