"""Shared pytest fixtures for the interfaces tests.

`FakeSession` implements the exact public API that `graea.engine.runner.TestSession`
will expose (see docs/dev/AGENT-RULES.md instructions to the "interfaces" agent), with
canned, deterministic models — no Telegram credentials, no network, no real DuckDB.
It is used to monkeypatch each interface module's session factory
(`mcp_server._session_factory`, `cli._session_factory`, `http._session_factory`).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

import pytest
from PIL import Image as PILImage

from graea.config import Settings
from graea.models import (
    Action,
    ActionKind,
    AssertionResult,
    AssertionSpec,
    DiffReport,
    Health,
    RunSummary,
    Scenario,
    Screenshot,
    StepDiff,
    StepResult,
    VisionFinding,
    VisionReading,
    VisionSeenMessage,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _write_png(path: Path, color=(30, 30, 30)) -> Screenshot:
    """Write a real tiny PNG via Pillow and return a Screenshot model for it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = PILImage.new("RGB", (320, 240), color=color)
    img.save(path, format="PNG")
    data = path.read_bytes()
    return Screenshot(
        path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        width=320,
        height=240,
        taken_at=_utcnow(),
        region="chat",
    )


class FakeSession:
    """Canned stand-in for `graea.engine.runner.TestSession`.

    Implements the same async method surface (constructor included) so it can be
    dropped in wherever an interface module builds/uses a `TestSession`.
    """

    def __init__(self, settings: Settings, transport=None, web=None, reader=None, store=None):
        self.settings = settings
        self._shots_dir = Path(settings.shots)
        self._shots_dir.mkdir(parents=True, exist_ok=True)
        self._started = False
        self.run_id: Optional[str] = None
        self.scenario: Optional[str] = None
        self._step_idx = 0
        self._last_step: Optional[StepResult] = None

    # -- lifecycle ---------------------------------------------------
    async def start(self) -> Health:
        self._started = True
        return await self.health()

    async def stop(self) -> None:
        self._started = False

    async def health(self) -> Health:
        return Health(
            mtproto_connected=True,
            mtproto_user="+15551234567",
            web_logged_in=True,
            web_error=None,
            vision_provider="ocr",
            vision_model=None,
            vision_ok=True,
            ocr_available=True,
            db_path=str(self.settings.db),
            bot=self.settings.bot_username() or "@testbot",
            active_run_id=self.run_id,
        )

    # -- run lifecycle -------------------------------------------------
    async def start_run(self, scenario: str = "adhoc", bot: Optional[str] = None,
                         source_path: Optional[str] = None, notes: Optional[str] = None) -> RunSummary:
        self._step_idx = 0
        self.scenario = scenario
        self.run_id = "r0001"
        return RunSummary(
            run_id=self.run_id,
            scenario=scenario,
            bot_username=bot or "@testbot",
            fingerprint="fake123",
            started_at=_utcnow(),
            status="running",
            steps=0,
            assertions_total=0,
            assertions_passed=0,
            previous_run_id=None,
            progress="first run: 0/0 assertions passing",
            step_results=[],
            notes=notes,
        )

    def _make_step(self, action: Action, expect: Optional[list[AssertionSpec]], name: Optional[str],
                    force_fail: bool = False) -> StepResult:
        idx = self._step_idx
        self._step_idx += 1
        shot = _write_png(self._shots_dir / f"step{idx}.png")
        vision = VisionReading(
            provider="ocr",
            model=None,
            description="A chat bubble reading 'hello from bot'.",
            messages_seen=[VisionSeenMessage(sender="bot", text_as_rendered="hello from bot", buttons=[])],
            issues=[] if not force_fail else [VisionFinding(severity="high", kind="mismatch", detail="text mismatch")],
            ocr_text="hello from bot",
            latency_ms=5,
            error=None,
        )
        specs = expect or []
        assertions = [
            AssertionResult(
                name=s.label(),
                kind=s.kind,
                passed=(s.value != "FAIL") and not force_fail,
                actual="hello from bot",
                message="ok" if (s.value != "FAIL" and not force_fail) else "expected value not found",
            )
            for s in specs
        ]
        step = StepResult(
            run_id=self.run_id or "r0001",
            step_id=f"{self.run_id or 'r0001'}s{idx:03d}",
            idx=idx,
            name=name or action.kind.value,
            action=action,
            sent_at=_utcnow(),
            done_at=_utcnow(),
            timed_out=False,
            events=[],
            replies=[],
            edits=[],
            deleted_ids=[],
            screenshot=shot,
            screenshot_error=None,
            vision=vision,
            assertions=assertions,
            diff=StepDiff(summary="no previous run"),
            progress="first run",
            notes=[],
        )
        self._last_step = step
        return step

    async def step(self, action: Action, expect: Optional[list[AssertionSpec]] = None,
                    name: Optional[str] = None) -> StepResult:
        return self._make_step(action, expect, name)

    async def submit_reading(self, description: str, issues=None, messages_seen=None,
                             step_id: Optional[str] = None, model: Optional[str] = None) -> StepResult:
        from graea.models import VisionFinding, VisionReading
        step = self.last_step or self._make_step(Action(kind="look"), None, None)
        step.vision = VisionReading(provider="caller", description=description,
                                    issues=[VisionFinding.model_validate(i) for i in (issues or [])])
        return step

    async def assert_last(self, specs: list[AssertionSpec]) -> list[AssertionResult]:
        return [
            AssertionResult(
                name=s.label(), kind=s.kind,
                passed=s.value != "FAIL",
                actual="hello from bot",
                message="ok" if s.value != "FAIL" else "expected value not found",
            )
            for s in specs
        ]

    async def run_scenario(self, scenario: Union[Scenario, str, Path]) -> RunSummary:
        name = scenario.name if isinstance(scenario, Scenario) else str(scenario)
        many_fail = "manyfail" in name
        partial_fail = "partialfail" in name
        self.run_id = "r0002"
        self.scenario = name
        n = 8 if many_fail else 4
        step_results = []
        total = 0
        passed = 0
        for i in range(n):
            if many_fail:
                force_fail = True
            elif partial_fail:
                force_fail = i % 2 == 1
            else:
                force_fail = False
            action = Action(kind=ActionKind.send_text, text=f"step {i}")
            specs = [AssertionSpec(kind="text_contains", value="hello", name=f"contains_{i}")]
            step = self._make_step(action, specs, f"step-{i}", force_fail=force_fail)
            step_results.append(step)
            total += len(step.assertions)
            passed += sum(1 for a in step.assertions if a.passed)
        return RunSummary(
            run_id=self.run_id,
            scenario=name,
            bot_username="@testbot",
            fingerprint="fake123",
            started_at=_utcnow(),
            ended_at=_utcnow(),
            status="failed" if passed < total else "passed",
            steps=n,
            assertions_total=total,
            assertions_passed=passed,
            previous_run_id="r0001",
            progress=f"{passed}/{total} assertions passing",
            step_results=step_results,
            notes=None,
        )

    async def end_run(self, status: Optional[str] = None, notes: Optional[str] = None) -> RunSummary:
        return RunSummary(
            run_id=self.run_id or "r0001",
            scenario=self.scenario or "adhoc",
            bot_username="@testbot",
            fingerprint="fake123",
            started_at=_utcnow(),
            ended_at=_utcnow(),
            status=status or "passed",
            steps=self._step_idx,
            assertions_total=1,
            assertions_passed=1,
            previous_run_id=None,
            progress="1/1 assertions passing",
            step_results=[],
            notes=notes,
        )

    # -- reads -----------------------------------------------------
    def diff(self, scenario: Optional[str] = None, run_a: Optional[str] = None,
              run_b: Optional[str] = None) -> DiffReport:
        return DiffReport(
            scenario=scenario or self.scenario or "adhoc",
            run_a=run_a or "r0001",
            run_b=run_b or "r0002",
            fingerprint_a="fake123",
            fingerprint_b="fake456",
            fixed=["step-0/contains_0"],
            regressed=[],
            still_failing=["step-1/contains_1"],
            still_passing=["step-2/contains_2"],
            new_assertions=[],
            missing_steps=[],
            summary="1 fixed, 0 regressed, 1 still failing, 1 still passing",
        )

    def history(self, scenario: str, assertion: Optional[str] = None, limit: int = 50) -> list[dict]:
        return [
            {"run_id": "r0001", "fingerprint": "fake123", "started_at": _utcnow().isoformat(),
             "step_name": "step-0", "assertion": assertion or "contains_0", "passed": True, "actual": "hello from bot"},
        ][:limit]

    def sql(self, query: str) -> list[dict]:
        q = query.strip().lower()
        if not (q.startswith("select") or q.startswith("with")):
            raise ValueError("only read-only SELECT/WITH statements are allowed")
        return [{"run_id": "r0001", "scenario": "adhoc", "status": "passed"}]

    @property
    def last_step(self) -> Optional[StepResult]:
        return self._last_step


@pytest.fixture(autouse=True)
def _no_network_update_checks(monkeypatch):
    """Every test runs offline by default (graea/version.py:check_for_update
    short-circuits on GRAEA_OFFLINE=1) so the CLI's every-command banner and
    the MCP session-start check never hit the network in the suite. Tests
    that specifically exercise check_for_update unset this themselves."""
    monkeypatch.setenv("GRAEA_OFFLINE", "1")


@pytest.fixture
def fake_settings(tmp_path) -> Settings:
    return Settings(
        db=tmp_path / "graea.duckdb",
        shots=tmp_path / "shots",
        session=tmp_path / "graea.session",
        web_profile=tmp_path / "web-profile",
        bot="@testbot",
    )


@pytest.fixture
def fake_session(fake_settings) -> FakeSession:
    return FakeSession(fake_settings)


@pytest.fixture
def patched_interfaces(monkeypatch, fake_session, fake_settings):
    """Monkeypatch every interface module's session factory to return `fake_session`."""
    import graea.interfaces.cli as cli
    import graea.interfaces.http as http
    import graea.interfaces.mcp_server as mcp_server

    async def mcp_factory():
        if not fake_session._started:
            await fake_session.start()
        return fake_session

    monkeypatch.setattr(mcp_server, "_session_factory", mcp_factory)
    monkeypatch.setattr(mcp_server, "_session", None)

    def cli_factory(settings):
        return fake_session

    monkeypatch.setattr(cli, "_session_factory", cli_factory)
    monkeypatch.setattr(cli, "_get_settings", lambda: fake_settings)

    def http_factory(settings):
        return fake_session

    monkeypatch.setattr(http, "_session_factory", http_factory)
    monkeypatch.setattr(http, "_get_settings", lambda: fake_settings)

    return fake_session
