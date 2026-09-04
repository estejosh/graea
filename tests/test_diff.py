"""Tests for graea.engine.diff: diff_steps, diff_runs, run_progress."""
from __future__ import annotations

from datetime import datetime, timezone

from graea.engine.diff import diff_runs, diff_steps, run_progress
from graea.models import (
    Action,
    ActionKind,
    AssertionResult,
    ObservationEvent,
    ObservedMessage,
    Screenshot,
    VisionFinding,
    VisionReading,
)
from graea.store import Store

NOW = datetime.now(timezone.utc)


def _msg(msg_id, text) -> ObservedMessage:
    return ObservedMessage(message_id=msg_id, chat_id=1, from_bot=True, date=NOW,
                            text=text, rendered_text=text)


def _action() -> Action:
    return Action(kind=ActionKind.send_command, text="/start")


def _make_run(store, scenario, *, greet_pass, count_pass, text="Welcome!", issues=0):
    run = store.create_run(scenario, "@bot", "fp1")
    step_id = store.add_step(run.run_id, 0, "start", _action(), NOW, NOW, 8000, False)
    store.add_observation(step_id, ObservationEvent(kind="new", message=_msg(1, text)))
    shot_id = store.add_screenshot(step_id, Screenshot(path="p.png", sha256="x", width=1, height=1))
    findings = [VisionFinding(severity="high", kind="other", detail="x") for _ in range(issues)]
    store.add_vision(shot_id, VisionReading(provider="ocr", description=text, issues=findings))
    store.add_assertion(step_id, AssertionResult(name="greets", kind="text_contains",
                                                  passed=greet_pass, actual=text))
    store.add_assertion(step_id, AssertionResult(name="count", kind="reply_count",
                                                  passed=count_pass, actual="1"))
    store.end_run(run.run_id, "passed" if greet_pass and count_pass else "failed")
    return run.run_id


def test_diff_steps_fixed_and_regressed():
    store = Store(":memory:")
    run1 = _make_run(store, "demo", greet_pass=False, count_pass=True)
    run2 = _make_run(store, "demo", greet_pass=True, count_pass=False)

    prev_step = store.find_step(run1, "start")
    cur_step = store.find_step(run2, "start")

    sd = diff_steps(cur_step, prev_step)
    assert sd.fixed == ["greets"]
    assert sd.regressed == ["count"]
    assert sd.still_failing == []
    assert sd.still_passing == []


def test_diff_steps_no_previous_reports_new():
    store = Store(":memory:")
    run1 = _make_run(store, "demo", greet_pass=True, count_pass=True)
    step = store.find_step(run1, "start")
    sd = diff_steps(step, None)
    assert set(sd.new_assertions) == {"greets", "count"}
    assert sd.fixed == [] and sd.regressed == []


def test_diff_steps_text_and_keyboard_changed():
    store = Store(":memory:")
    run1 = _make_run(store, "demo", greet_pass=True, count_pass=True, text="Hello")
    run2 = _make_run(store, "demo", greet_pass=True, count_pass=True, text="Howdy")
    prev_step = store.find_step(run1, "start")
    cur_step = store.find_step(run2, "start")
    sd = diff_steps(cur_step, prev_step)
    assert sd.text_changed is True
    assert sd.keyboard_changed is False


def test_diff_steps_vision_issue_delta():
    store = Store(":memory:")
    run1 = _make_run(store, "demo", greet_pass=True, count_pass=True, issues=0)
    run2 = _make_run(store, "demo", greet_pass=True, count_pass=True, issues=2)
    prev_step = store.find_step(run1, "start")
    cur_step = store.find_step(run2, "start")
    sd = diff_steps(cur_step, prev_step)
    assert sd.vision_issue_delta == 2


def test_diff_runs_matches_by_step_name():
    store = Store(":memory:")
    run1 = _make_run(store, "demo", greet_pass=False, count_pass=True)
    run2 = _make_run(store, "demo", greet_pass=True, count_pass=True)
    report = diff_runs(store, run1, run2)
    assert report.run_a == run1
    assert report.run_b == run2
    assert "start/greets" in report.fixed
    assert "start/count" in report.still_passing
    assert report.missing_steps == []


def test_run_progress_delegates_to_store():
    store = Store(":memory:")
    run1 = _make_run(store, "demo", greet_pass=True, count_pass=True)
    line = run_progress(store, run1)
    assert line == store.progress_line(run1)
    assert "first run" in line
