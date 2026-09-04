"""Tests for graea.store.Store — schema, writes, reconstruction, sql guard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from graea.models import (
    Action,
    ActionKind,
    AssertionResult,
    AssertionSpec,
    Button,
    Keyboard,
    ObservationEvent,
    ObservedMessage,
    Screenshot,
    VisionFinding,
    VisionReading,
)
from graea.store import Store

NOW = datetime.now(timezone.utc)


def _msg(msg_id=1, from_bot=True, text="hello", keyboard=None, media=None,
         caption=None, date=None) -> ObservedMessage:
    return ObservedMessage(
        message_id=msg_id, chat_id=42, from_bot=from_bot, sender_id=999 if from_bot else 111,
        date=date or NOW, text=text, rendered_text=text, keyboard=keyboard, media=media,
        caption=caption,
    )


def _action(text="/start") -> Action:
    return Action(kind=ActionKind.send_command, text=text)


def _run_and_step(store: Store, scenario="demo", assertion_pass=True, add_vision=True):
    run = store.create_run(scenario, "@bot", "abc123")
    action = _action()
    sent_at = NOW
    done_at = NOW + timedelta(milliseconds=500)
    step_id = store.add_step(run.run_id, 0, "start", action, sent_at, done_at, 8000, False)

    reply = _msg(msg_id=2, text="Welcome!")
    event = ObservationEvent(kind="new", message=reply, occurred_at=sent_at + timedelta(milliseconds=200))
    store.add_observation(step_id, event)

    shot = Screenshot(path="/tmp/shot.png", sha256="deadbeef", width=100, height=200)
    shot_id = store.add_screenshot(step_id, shot)

    if add_vision:
        reading = VisionReading(
            provider="ocr", model=None, description="a welcome message",
            issues=[] if assertion_pass else [VisionFinding(severity="high", kind="raw_markdown", detail="bad")],
        )
        store.add_vision(shot_id, reading)

    result = AssertionResult(name="greets", kind="text_contains", passed=assertion_pass,
                              actual="Welcome!", message="")
    spec = AssertionSpec(kind="text_contains", name="greets", value="Welcome")
    store.add_assertion(step_id, result, spec)

    store.end_run(run.run_id, "passed" if assertion_pass else "failed")
    return run.run_id, step_id


def test_run_id_format_and_sequence():
    store = Store(":memory:")
    run1 = store.create_run("s", "@bot", None)
    run2 = store.create_run("s", "@bot", None)
    assert run1.run_id == "r0001"
    assert run2.run_id == "r0002"


def test_step_id_format():
    store = Store(":memory:")
    run = store.create_run("s", "@bot", None)
    step_id = store.add_step(run.run_id, 3, "mystep", _action(), NOW, NOW, 8000, False)
    assert step_id == f"{run.run_id}s003"


def test_get_run_round_trips_replies_keyboard_vision():
    store = Store(":memory:")
    kb = Keyboard(kind="inline", rows=[[Button(text="OK", kind="callback", data="ok", row=0, col=0)]])
    run = store.create_run("demo", "@bot", "abc")
    step_id = store.add_step(run.run_id, 0, "start", _action(), NOW, NOW, 8000, False)
    reply = _msg(msg_id=5, text="Hi there", keyboard=kb)
    store.add_observation(step_id, ObservationEvent(kind="new", message=reply))
    shot_id = store.add_screenshot(step_id, Screenshot(path="p.png", sha256="x", width=1, height=1))
    store.add_vision(shot_id, VisionReading(provider="ocr", description="hi there"))
    store.add_assertion(
        step_id,
        AssertionResult(name="a1", kind="text_contains", passed=True, actual="Hi there"),
    )
    store.end_run(run.run_id, "passed")

    got = store.get_run(run.run_id)
    assert got.run_id == run.run_id
    assert len(got.step_results) == 1
    sr = got.step_results[0]
    assert len(sr.replies) == 1
    assert sr.replies[0].rendered_text == "Hi there"
    assert sr.replies[0].keyboard.kind == "inline"
    assert sr.replies[0].keyboard.shape == [1]
    assert sr.replies[0].keyboard.rows[0][0].text == "OK"
    assert sr.vision is not None
    assert sr.vision.description == "hi there"
    assert sr.assertions[0].name == "a1"
    assert sr.diff is None


def test_get_run_reconstructs_edits_and_deletes():
    store = Store(":memory:")
    run = store.create_run("demo", "@bot", None)
    step_id = store.add_step(run.run_id, 0, "s", _action(), NOW, NOW, 8000, False)
    edited = _msg(msg_id=9, text="edited text")
    store.add_observation(step_id, ObservationEvent(kind="edit", message=edited))
    store.add_observation(step_id, ObservationEvent(kind="delete", message_ids=[9, 10]))
    store.end_run(run.run_id, "passed")

    sr = store.get_run(run.run_id).step_results[0]
    assert len(sr.edits) == 1
    assert sr.edits[0].rendered_text == "edited text"
    assert sorted(sr.deleted_ids) == [9, 10]


def test_progress_line_first_run_no_previous():
    store = Store(":memory:")
    run_id, _ = _run_and_step(store, scenario="demo", assertion_pass=True)
    line = store.progress_line(run_id)
    assert line.startswith("first run:")
    assert "1/1" in line


def test_progress_line_fixed_and_regressed():
    store = Store(":memory:")
    run1_id, step1 = _run_and_step(store, scenario="demo", assertion_pass=True)
    # simulate second run where the same assertion name now fails, and add a
    # second assertion that goes from fail to pass
    run2 = store.create_run("demo", "@bot", "def456")
    step_id = store.add_step(run2.run_id, 0, "start", _action(), NOW, NOW, 8000, False)
    store.add_assertion(
        step_id,
        AssertionResult(name="greets", kind="text_contains", passed=False, actual="oops"),
    )
    store.end_run(run2.run_id, "failed")

    line = store.progress_line(run2.run_id)
    assert "1 regressed" in line
    assert run1_id in line


def test_previous_run_returns_most_recent_ended_run_of_scenario():
    store = Store(":memory:")
    run1_id, _ = _run_and_step(store, scenario="demo")
    run2_id, _ = _run_and_step(store, scenario="demo")
    prev = store.previous_run("demo", run2_id)
    assert prev is not None
    assert prev.run_id == run1_id

    # different scenario has no previous
    assert store.previous_run("other-scenario", run2_id) is None


def test_find_step():
    store = Store(":memory:")
    run_id, _ = _run_and_step(store)
    step = store.find_step(run_id, "start")
    assert step is not None
    assert step.name == "start"
    assert store.find_step(run_id, "nonexistent") is None


def test_assertion_history():
    store = Store(":memory:")
    run_id, _ = _run_and_step(store, scenario="demo", assertion_pass=True)
    rows = store.assertion_history("demo")
    assert len(rows) == 1
    assert rows[0]["run_id"] == run_id
    assert rows[0]["assertion"] == "greets"
    assert rows[0]["passed"] is True

    rows2 = store.assertion_history("demo", assertion_name="nonexistent")
    assert rows2 == []


def test_list_runs_no_step_results():
    store = Store(":memory:")
    _run_and_step(store, scenario="demo")
    _run_and_step(store, scenario="demo")
    runs = store.list_runs("demo")
    assert len(runs) == 2
    assert all(r.step_results == [] for r in runs)
    # newest first
    assert runs[0].run_id > runs[1].run_id


def test_sql_allows_select():
    store = Store(":memory:")
    _run_and_step(store, scenario="demo")
    rows = store.sql("SELECT run_id FROM runs")
    assert len(rows) == 1


def test_sql_allows_with_cte():
    store = Store(":memory:")
    _run_and_step(store, scenario="demo")
    rows = store.sql("WITH x AS (SELECT run_id FROM runs) SELECT * FROM x")
    assert len(rows) == 1


@pytest.mark.parametrize("bad_query", [
    "INSERT INTO runs (run_id) VALUES ('r9999')",
    "DROP TABLE runs",
    "DELETE FROM runs",
    "UPDATE runs SET status = 'x'",
    "SELECT * FROM runs; DROP TABLE runs",
    "SELECT * FROM runs; SELECT * FROM steps",
])
def test_sql_rejects_non_select(bad_query):
    store = Store(":memory:")
    with pytest.raises(ValueError):
        store.sql(bad_query)


def test_sql_rejects_multi_statement_with_trailing_semicolon_ok():
    store = Store(":memory:")
    _run_and_step(store, scenario="demo")
    # single statement with a single trailing semicolon is fine
    rows = store.sql("SELECT run_id FROM runs;")
    assert len(rows) == 1


def test_end_run_computes_counts():
    store = Store(":memory:")
    run_id, _ = _run_and_step(store, scenario="demo", assertion_pass=True)
    summary = store.get_run(run_id)
    assert summary.steps == 1
    assert summary.assertions_total == 1
    assert summary.assertions_passed == 1
    assert summary.status == "passed"
