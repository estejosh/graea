"""DuckDB-backed store: schema, writes, reconstruction, and read-only SQL.

Public API (all sync — the engine calls these from async code via an
executor; DuckDB is fast enough not to need async itself):

    Store(path)                                            -- path or ":memory:"
    create_run(scenario, bot_username, fingerprint, notes=None) -> RunSummary
    end_run(run_id, status, notes=None) -> RunSummary
    add_step(run_id, idx, name, action, sent_at, done_at, timeout_ms, timed_out) -> step_id
    add_observation(step_id, event) -> obs_id
    add_screenshot(step_id, shot) -> shot_id
    add_vision(shot_id, reading) -> reading_id
    add_assertion(step_id, result, spec=None) -> assert_id
    get_run(run_id) -> RunSummary                          -- with step_results
    list_runs(scenario=None, limit=20) -> list[RunSummary]  -- no step_results
    previous_run(scenario, before_run_id) -> RunSummary | None
    find_step(run_id, name) -> StepResult | None
    assertion_history(scenario, assertion_name=None, limit=50) -> list[dict]
    sql(query, params=None) -> list[dict]                  -- read-only SELECT/WITH
    progress_line(run_id) -> str
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

import duckdb

from graea.models import (
    Action,
    AssertionResult,
    AssertionSpec,
    ObservationEvent,
    RunSummary,
    Screenshot,
    StepResult,
    VisionReading,
)

_SCHEMA_STATEMENTS = [
    "CREATE SEQUENCE IF NOT EXISTS run_id_seq START 1",
    "CREATE SEQUENCE IF NOT EXISTS obs_seq START 1",
    "CREATE SEQUENCE IF NOT EXISTS shot_seq START 1",
    "CREATE SEQUENCE IF NOT EXISTS vision_seq START 1",
    "CREATE SEQUENCE IF NOT EXISTS assert_seq START 1",
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id VARCHAR PRIMARY KEY,
        scenario VARCHAR,
        bot_username VARCHAR,
        fingerprint VARCHAR,
        started_at TIMESTAMP,
        ended_at TIMESTAMP,
        status VARCHAR,
        notes VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS steps (
        step_id VARCHAR PRIMARY KEY,
        run_id VARCHAR,
        idx INTEGER,
        name VARCHAR,
        action_json VARCHAR,
        sent_at TIMESTAMP,
        done_at TIMESTAMP,
        timeout_ms INTEGER,
        timed_out BOOLEAN
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS observations (
        obs_id VARCHAR PRIMARY KEY,
        seq BIGINT DEFAULT nextval('obs_seq'),
        step_id VARCHAR,
        kind VARCHAR,
        message_id BIGINT,
        chat_id BIGINT,
        occurred_at TIMESTAMP,
        payload_json VARCHAR,
        rendered_text VARCHAR,
        has_keyboard BOOLEAN,
        keyboard_json VARCHAR,
        media_json VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS screenshots (
        shot_id VARCHAR PRIMARY KEY,
        seq BIGINT DEFAULT nextval('shot_seq'),
        step_id VARCHAR,
        path VARCHAR,
        sha256 VARCHAR,
        width INTEGER,
        height INTEGER,
        taken_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vision_readings (
        reading_id VARCHAR PRIMARY KEY,
        seq BIGINT DEFAULT nextval('vision_seq'),
        shot_id VARCHAR,
        provider VARCHAR,
        model VARCHAR,
        description VARCHAR,
        findings_json VARCHAR,
        ocr_text VARCHAR,
        latency_ms INTEGER,
        error VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS assertions (
        assert_id VARCHAR PRIMARY KEY,
        seq BIGINT DEFAULT nextval('assert_seq'),
        step_id VARCHAR,
        name VARCHAR,
        kind VARCHAR,
        spec_json VARCHAR,
        passed BOOLEAN,
        actual VARCHAR,
        message VARCHAR
    )
    """,
]

_READ_ONLY_PREFIX = re.compile(r"^(SELECT|WITH)\b", re.IGNORECASE)


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """DuckDB TIMESTAMP columns round-trip as naive datetimes; reattach UTC
    (everything this store writes is already UTC) for a well-formed model."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _strip_sql_comments(query: str) -> str:
    # strip -- line comments
    no_line = re.sub(r"--[^\n]*", "", query)
    # strip /* */ block comments
    no_block = re.sub(r"/\*.*?\*/", "", no_line, flags=re.DOTALL)
    return no_block


class Store:
    def __init__(self, path: Union[str, Path]):
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn = duckdb.connect(self._path)
        # Avoid duckdb's ICU timezone conversion requiring the (unavailable) pytz
        # package when the host's local timezone isn't UTC.
        self._conn.execute("SET TimeZone='UTC'")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            for stmt in _SCHEMA_STATEMENTS:
                self._conn.execute(stmt)

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------

    def _next_run_id(self) -> str:
        n = self._conn.execute("SELECT nextval('run_id_seq')").fetchone()[0]
        return f"r{int(n):04d}"

    @staticmethod
    def _uuid() -> str:
        return uuid.uuid4().hex

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_run(self, scenario: str, bot_username: Optional[str],
                    fingerprint: Optional[str], notes: Optional[str] = None) -> RunSummary:
        """Start a new run; returns the RunSummary with status='running'."""
        with self._lock:
            run_id = self._next_run_id()
            started_at = datetime.now(timezone.utc)
            self._conn.execute(
                "INSERT INTO runs (run_id, scenario, bot_username, fingerprint, "
                "started_at, ended_at, status, notes) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                [run_id, scenario, bot_username, fingerprint, started_at, "running", notes],
            )
        return RunSummary(
            run_id=run_id, scenario=scenario, bot_username=bot_username,
            fingerprint=fingerprint, started_at=started_at, ended_at=None,
            status="running", notes=notes,
        )

    def end_run(self, run_id: str, status: str, notes: Optional[str] = None) -> RunSummary:
        """Mark a run ended, compute step/assertion counts and progress, return RunSummary."""
        ended_at = datetime.now(timezone.utc)
        with self._lock:
            if notes is not None:
                self._conn.execute(
                    "UPDATE runs SET ended_at = ?, status = ?, notes = ? WHERE run_id = ?",
                    [ended_at, status, notes, run_id],
                )
            else:
                self._conn.execute(
                    "UPDATE runs SET ended_at = ?, status = ? WHERE run_id = ?",
                    [ended_at, status, run_id],
                )
        return self._run_summary(run_id, with_progress=True)

    def add_step(self, run_id: str, idx: int, name: str, action: Action,
                 sent_at: datetime, done_at: datetime, timeout_ms: int,
                 timed_out: bool) -> str:
        step_id = f"{run_id}s{idx:03d}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO steps (step_id, run_id, idx, name, action_json, sent_at, "
                "done_at, timeout_ms, timed_out) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [step_id, run_id, idx, name, json.dumps(action.model_dump(mode="json")),
                 sent_at, done_at, timeout_ms, timed_out],
            )
        return step_id

    def add_observation(self, step_id: str, event: ObservationEvent) -> str:
        obs_id = self._uuid()
        message = event.message
        message_id = message.message_id if message else None
        chat_id = message.chat_id if message else None
        rendered_text = message.rendered_text if message else None
        has_keyboard = bool(message and message.keyboard)
        keyboard_json = (
            json.dumps(message.keyboard.model_dump(mode="json"))
            if message and message.keyboard else None
        )
        media_json = (
            json.dumps(message.media.model_dump(mode="json"))
            if message and message.media else None
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO observations (obs_id, step_id, kind, message_id, chat_id, "
                "occurred_at, payload_json, rendered_text, has_keyboard, keyboard_json, "
                "media_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [obs_id, step_id, event.kind, message_id, chat_id, event.occurred_at,
                 json.dumps(event.model_dump(mode="json")), rendered_text, has_keyboard,
                 keyboard_json, media_json],
            )
        return obs_id

    def add_screenshot(self, step_id: str, shot: Screenshot) -> str:
        shot_id = self._uuid()
        with self._lock:
            self._conn.execute(
                "INSERT INTO screenshots (shot_id, step_id, path, sha256, width, height, "
                "taken_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [shot_id, step_id, shot.path, shot.sha256, shot.width, shot.height,
                 shot.taken_at],
            )
        return shot_id

    def add_vision(self, shot_id: str, reading: VisionReading) -> str:
        reading_id = self._uuid()
        findings_json = json.dumps([f.model_dump(mode="json") for f in reading.issues])
        with self._lock:
            self._conn.execute(
                "INSERT INTO vision_readings (reading_id, shot_id, provider, model, "
                "description, findings_json, ocr_text, latency_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [reading_id, shot_id, reading.provider, reading.model, reading.description,
                 findings_json, reading.ocr_text, reading.latency_ms, reading.error],
            )
        return reading_id

    def add_assertion(self, step_id: str, result: AssertionResult,
                       spec: Optional[AssertionSpec] = None) -> str:
        assert_id = self._uuid()
        spec_json = json.dumps(spec.model_dump(mode="json")) if spec is not None else None
        with self._lock:
            self._conn.execute(
                "INSERT INTO assertions (assert_id, step_id, name, kind, spec_json, "
                "passed, actual, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [assert_id, step_id, result.name, result.kind, spec_json, result.passed,
                 result.actual, result.message],
            )
        return assert_id

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _run_row(self, run_id: str) -> Optional[dict]:
        rows = self._query("SELECT * FROM runs WHERE run_id = ?", [run_id])
        return rows[0] if rows else None

    def _run_summary(self, run_id: str, with_progress: bool) -> RunSummary:
        row = self._run_row(run_id)
        if row is None:
            raise ValueError(f"no such run: {run_id}")
        assertion_rows = self._query(
            "SELECT a.passed FROM assertions a JOIN steps s ON a.step_id = s.step_id "
            "WHERE s.run_id = ?",
            [run_id],
        )
        step_count_row = self._query(
            "SELECT COUNT(*) AS n FROM steps WHERE run_id = ?", [run_id]
        )
        steps_n = step_count_row[0]["n"] if step_count_row else 0
        total = len(assertion_rows)
        passed = sum(1 for r in assertion_rows if r["passed"])
        prev = self.previous_run(row["scenario"], run_id)
        progress = self.progress_line(run_id) if with_progress else ""
        return RunSummary(
            run_id=row["run_id"], scenario=row["scenario"],
            bot_username=row["bot_username"], fingerprint=row["fingerprint"],
            started_at=_to_utc(row["started_at"]), ended_at=_to_utc(row["ended_at"]),
            status=row["status"], steps=steps_n, assertions_total=total,
            assertions_passed=passed,
            previous_run_id=prev.run_id if prev else None,
            progress=progress, notes=row["notes"],
        )

    def get_run(self, run_id: str) -> RunSummary:
        """Full RunSummary with step_results reconstructed (diff left None)."""
        summary = self._run_summary(run_id, with_progress=True)
        step_rows = self._query(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY idx", [run_id]
        )
        step_results: list[StepResult] = []
        for srow in step_rows:
            step_results.append(self._build_step_result(srow))
        summary.step_results = step_results
        summary.steps = len(step_results)
        return summary

    def _build_step_result(self, srow: dict) -> StepResult:
        step_id = srow["step_id"]
        action = Action(**json.loads(srow["action_json"]))

        obs_rows = self._query(
            "SELECT * FROM observations WHERE step_id = ? ORDER BY seq", [step_id]
        )
        events: list[ObservationEvent] = []
        for orow in obs_rows:
            events.append(ObservationEvent(**json.loads(orow["payload_json"])))

        replies = [e.message for e in events if e.kind == "new" and e.message
                   and e.message.from_bot]
        edits = [e.message for e in events if e.kind == "edit" and e.message]
        deleted_ids: list[int] = []
        for e in events:
            if e.kind == "delete":
                deleted_ids.extend(e.message_ids)

        shot_rows = self._query(
            "SELECT * FROM screenshots WHERE step_id = ? ORDER BY seq DESC LIMIT 1",
            [step_id],
        )
        screenshot = None
        vision = None
        if shot_rows:
            srow2 = shot_rows[0]
            screenshot = Screenshot(
                path=srow2["path"], sha256=srow2["sha256"], width=srow2["width"],
                height=srow2["height"], taken_at=_to_utc(srow2["taken_at"]),
            )
            vision_rows = self._query(
                "SELECT * FROM vision_readings WHERE shot_id = ? ORDER BY seq DESC LIMIT 1",
                [srow2["shot_id"]],
            )
            if vision_rows:
                vrow = vision_rows[0]
                findings = [
                    {**f} for f in json.loads(vrow["findings_json"] or "[]")
                ]
                vision = VisionReading(
                    provider=vrow["provider"], model=vrow["model"],
                    description=vrow["description"] or "", issues=findings,
                    ocr_text=vrow["ocr_text"], latency_ms=vrow["latency_ms"],
                    error=vrow["error"],
                )

        assert_rows = self._query(
            "SELECT * FROM assertions WHERE step_id = ? ORDER BY seq", [step_id]
        )
        assertions = [
            AssertionResult(name=a["name"], kind=a["kind"], passed=bool(a["passed"]),
                             actual=a["actual"], message=a["message"] or "")
            for a in assert_rows
        ]

        return StepResult(
            run_id=srow["run_id"], step_id=step_id, idx=srow["idx"], name=srow["name"],
            action=action, sent_at=_to_utc(srow["sent_at"]), done_at=_to_utc(srow["done_at"]),
            timed_out=bool(srow["timed_out"]), events=events, replies=replies,
            edits=edits, deleted_ids=deleted_ids, screenshot=screenshot, vision=vision,
            assertions=assertions, diff=None, progress="",
        )

    def list_runs(self, scenario: Optional[str] = None, limit: int = 20) -> list[RunSummary]:
        """RunSummary rows, newest first, without step_results."""
        if scenario is not None:
            rows = self._query(
                "SELECT run_id FROM runs WHERE scenario = ? ORDER BY started_at DESC LIMIT ?",
                [scenario, limit],
            )
        else:
            rows = self._query(
                "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT ?", [limit]
            )
        return [self._run_summary(r["run_id"], with_progress=True) for r in rows]

    def previous_run(self, scenario: str, before_run_id: str) -> Optional[RunSummary]:
        """Most recent ended run of `scenario` started before `before_run_id`."""
        before_row = self._run_row(before_run_id)
        if before_row is None:
            rows = self._query(
                "SELECT run_id FROM runs WHERE scenario = ? AND status != 'running' "
                "AND ended_at IS NOT NULL ORDER BY started_at DESC LIMIT 1",
                [scenario],
            )
        else:
            rows = self._query(
                "SELECT run_id FROM runs WHERE scenario = ? AND run_id != ? "
                "AND status != 'running' AND ended_at IS NOT NULL "
                "AND started_at < ? ORDER BY started_at DESC LIMIT 1",
                [scenario, before_run_id, before_row["started_at"]],
            )
        if not rows:
            return None
        return self._run_summary(rows[0]["run_id"], with_progress=False)

    def find_step(self, run_id: str, name: str) -> Optional[StepResult]:
        rows = self._query(
            "SELECT * FROM steps WHERE run_id = ? AND name = ? ORDER BY idx LIMIT 1",
            [run_id, name],
        )
        if not rows:
            return None
        return self._build_step_result(rows[0])

    def assertion_history(self, scenario: str, assertion_name: Optional[str] = None,
                           limit: int = 50) -> list[dict]:
        """Rows: run_id, fingerprint, started_at, step_name, assertion, passed, actual."""
        base = (
            "SELECT r.run_id AS run_id, r.fingerprint AS fingerprint, "
            "r.started_at AS started_at, s.name AS step_name, a.name AS assertion, "
            "a.passed AS passed, a.actual AS actual "
            "FROM assertions a "
            "JOIN steps s ON a.step_id = s.step_id "
            "JOIN runs r ON s.run_id = r.run_id "
            "WHERE r.scenario = ?"
        )
        params: list[Any] = [scenario]
        if assertion_name is not None:
            base += " AND a.name = ?"
            params.append(assertion_name)
        base += " ORDER BY r.started_at DESC LIMIT ?"
        params.append(limit)
        return self._query(base, params)

    # ------------------------------------------------------------------
    # Progress / diff support
    # ------------------------------------------------------------------

    def progress_line(self, run_id: str) -> str:
        """Compare this run's assertions to the previous run of the same scenario."""
        row = self._run_row(run_id)
        if row is None:
            raise ValueError(f"no such run: {run_id}")
        scenario = row["scenario"]
        prev = self.previous_run(scenario, run_id)

        cur_rows = self._query(
            "SELECT s.name AS step_name, a.name AS assertion, a.passed AS passed "
            "FROM assertions a JOIN steps s ON a.step_id = s.step_id WHERE s.run_id = ?",
            [run_id],
        )
        cur_map = {(r["step_name"], r["assertion"]): bool(r["passed"]) for r in cur_rows}

        if prev is None:
            total = len(cur_rows)
            passed = sum(1 for v in cur_map.values() if v)
            return f"first run: {passed}/{total} assertions passing"

        prev_rows = self._query(
            "SELECT s.name AS step_name, a.name AS assertion, a.passed AS passed "
            "FROM assertions a JOIN steps s ON a.step_id = s.step_id WHERE s.run_id = ?",
            [prev.run_id],
        )
        prev_map = {(r["step_name"], r["assertion"]): bool(r["passed"]) for r in prev_rows}

        fixed = regressed = still_failing = still_passing = 0
        for key, cur_passed in cur_map.items():
            if key not in prev_map:
                continue
            prev_passed = prev_map[key]
            if prev_passed and cur_passed:
                still_passing += 1
            elif prev_passed and not cur_passed:
                regressed += 1
            elif not prev_passed and cur_passed:
                fixed += 1
            else:
                still_failing += 1

        return (
            f"{fixed} fixed, {regressed} regressed, {still_failing} still failing, "
            f"{still_passing} passing (vs {prev.run_id})"
        )

    # ------------------------------------------------------------------
    # Raw SQL (read-only)
    # ------------------------------------------------------------------

    def sql(self, query: str, params: Optional[list[Any]] = None) -> list[dict]:
        """Execute a single read-only SELECT/WITH statement. Raises ValueError otherwise."""
        stripped = _strip_sql_comments(query).strip()
        if not stripped:
            raise ValueError("empty query")
        # reject multiple statements: allow at most one trailing semicolon
        body = stripped[:-1] if stripped.endswith(";") else stripped
        if ";" in body:
            raise ValueError("only a single statement is allowed")
        if not _READ_ONLY_PREFIX.match(body.strip()):
            raise ValueError("only SELECT/WITH statements are allowed")
        return self._query(body, params or [])

    def _query(self, query: str, params: Optional[list[Any]] = None) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(query, params or [])
            cols = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
        return [dict(zip(cols, row)) for row in rows]
