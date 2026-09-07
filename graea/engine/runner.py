"""TestSession: the single orchestrator every interface (MCP, CLI, HTTP) drives.

One step = act -> wait -> observe -> screenshot -> read -> store -> diff ->
StepResult. See docs/SPEC.md and the class docstring below for the contract.

Public API:
    class TestSession(settings, transport=None, web=None, reader=None, store=None)
        async def start() -> Health
        async def stop() -> None
        async def health() -> Health
        async def start_run(scenario="adhoc", bot=None, source_path=None, notes=None) -> RunSummary
        async def step(action, expect=None, name=None) -> StepResult
        async def assert_last(specs) -> list[AssertionResult]
        async def run_scenario(scenario) -> RunSummary
        async def end_run(status=None, notes=None) -> RunSummary
        def diff(scenario=None, run_a=None, run_b=None) -> DiffReport
        def history(scenario, assertion=None, limit=50) -> list[dict]
        def sql(query) -> list[dict]
        @property last_step -> StepResult | None
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Optional, Union

from graea.client.mtproto import TelethonTransport
from graea.config import Settings
from graea.engine.assertions import evaluate_all
from graea.engine.diff import diff_runs, diff_steps
from graea.engine.fingerprint import fingerprint
from graea.engine.scenario import load_scenario
from graea.models import (
    Action,
    ActionKind,
    AssertionResult,
    AssertionSpec,
    DiffReport,
    Health,
    ObservationEvent,
    ObservedMessage,
    RunSummary,
    Scenario,
    StepResult,
    VisionReading,
    utcnow,
)
from graea.store import Store
from graea.visual.vision import make_reader, probe
from graea.visual.web import NullWeb, TelegramWeb


def _short_text(action: Action) -> str:
    """A short, filename/name-friendly fragment describing an action."""
    raw = action.text or action.button_text or action.file_path or ""
    raw = raw.strip().replace("\n", " ")
    return raw[:24] if raw else "step"


class TestSession:
    """One orchestrator per (settings, run history). Owns the transport, the
    visual eye, the vision reader, and the store, and turns Actions into
    fully-observed, stored, diffed StepResults.

    `transport`/`web`/`reader`/`store` may be injected (tests use fakes);
    left as `None` they are built from `settings` lazily inside `start()`.
    """

    def __init__(self, settings: Settings, transport=None, web=None,
                 reader=None, store: Optional[Store] = None):
        self.settings = settings
        self._transport = transport
        self._web = web
        self._reader = reader
        self._store = store

        self._web_disabled_reason: Optional[str] = None

        self.run_id: Optional[str] = None
        self.scenario_name: Optional[str] = None
        self.step_idx: int = 0
        self.last_replies: list[ObservedMessage] = []
        self._message_by_id: dict[int, ObservedMessage] = {}
        self._run_steps: list[StepResult] = []
        self._run_ok: bool = True
        self._last_step: Optional[StepResult] = None
        self._started = False

    # ------------------------------------------------------------------
    # accessors (raise a clear error if used before start())
    # ------------------------------------------------------------------

    @property
    def transport(self):
        if self._transport is None:
            raise RuntimeError("TestSession not started; call start() first")
        return self._transport

    @property
    def web(self):
        if self._web is None:
            raise RuntimeError("TestSession not started; call start() first")
        return self._web

    @property
    def reader(self):
        if self._reader is None:
            raise RuntimeError("TestSession not started; call start() first")
        return self._reader

    @property
    def store(self) -> Store:
        if self._store is None:
            raise RuntimeError("TestSession not started; call start() first")
        return self._store

    @property
    def last_step(self) -> Optional[StepResult]:
        """The most recently completed StepResult, or None before any step ran."""
        return self._last_step

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> Health:
        """Wire up transport/web/reader/store and return a Health snapshot.

        transport: built as TelethonTransport(settings) if not injected, then
        always connected. web: built as TelegramWeb(settings) if not
        injected; if starting/logging in fails AND it was built here (not
        injected), falls back to NullWeb with a note recorded for later
        steps. reader: built via make_reader(settings) if not injected, then
        probed best-effort (never blocks start on a vision failure). store:
        Store(settings.db) if not injected.
        """
        self.settings.ensure_dirs()

        if self._transport is None:
            self._transport = TelethonTransport(self.settings)
        try:
            await asyncio.wait_for(self._transport.connect(), timeout=self.settings.connect_timeout_s)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Telegram connect timed out after {self.settings.connect_timeout_s}s "
                "(first connect can be slow; re-run)"
            ) from exc

        web_was_none = self._web is None
        if web_was_none:
            self._web = TelegramWeb(self.settings)
        try:
            await self._web.start()
            logged_in = await self._web.is_logged_in()
            if not logged_in:
                self._web_disabled_reason = "web session not logged in"
        except Exception as e:
            self._web_disabled_reason = str(e)
            if web_was_none:
                self._web = NullWeb()

        if self._reader is None:
            self._reader = make_reader(self.settings)
        try:
            await probe(self._reader)
        except Exception:
            pass  # best effort only; vision_ok is reported by health()

        if self._store is None:
            self._store = Store(self.settings.db)

        self._started = True
        return await self.health()

    async def stop(self) -> None:
        """Disconnect the transport and stop the visual eye. Best-effort:
        never raises even if the underlying pieces error on shutdown."""
        try:
            if self._transport is not None:
                await self._transport.disconnect()
        except Exception:
            pass
        try:
            if self._web is not None:
                await self._web.stop()
        except Exception:
            pass

    async def health(self) -> Health:
        """A live snapshot of every subsystem's status, for graea_status()."""
        mtproto_connected = False
        mtproto_user = None
        try:
            mtproto_connected = bool(await self.transport.is_connected())
            mtproto_user = await self.transport.me()
        except Exception:
            pass

        web_logged_in: Optional[bool] = None
        web_error = self._web_disabled_reason
        try:
            web_logged_in = await self.web.is_logged_in()
        except Exception as e:
            web_error = web_error or str(e)

        vision_ok: Optional[bool] = None
        try:
            vision_ok = await probe(self.reader)
        except Exception:
            vision_ok = None

        return Health(
            mtproto_connected=mtproto_connected,
            mtproto_user=mtproto_user,
            web_logged_in=web_logged_in,
            web_error=web_error,
            vision_provider=getattr(self._reader, "name", "none"),
            vision_model=getattr(self._reader, "model", None),
            vision_ok=vision_ok,
            ocr_available=shutil.which("tesseract") is not None,
            db_path=str(self.settings.db),
            bot=self.settings.bot_username(),
            active_run_id=self.run_id,
        )

    # ------------------------------------------------------------------
    # runs
    # ------------------------------------------------------------------

    def _resolve_bot(self, bot: Optional[str]) -> str:
        candidate = bot or self.settings.bot_username()
        if not candidate:
            raise ValueError("no bot configured: pass bot=... or set GRAEA_BOT")
        return candidate if candidate.startswith("@") else f"@{candidate}"

    async def start_run(self, scenario: str = "adhoc", bot: Optional[str] = None,
                         source_path: Optional[str] = None,
                         notes: Optional[str] = None) -> RunSummary:
        """Resolve the target bot, open the chat on both eyes, fingerprint
        the bot source, and create a new run row. Resets step counters."""
        resolved_bot = self._resolve_bot(bot)
        await self.transport.open_chat(resolved_bot)
        try:
            await self.web.open_chat(resolved_bot)
        except Exception as e:
            self._web_disabled_reason = self._web_disabled_reason or str(e)

        fp = fingerprint(source_path)
        run_summary = self.store.create_run(scenario, resolved_bot, fp, notes)

        self.run_id = run_summary.run_id
        self.scenario_name = scenario
        self.step_idx = 0
        self.last_replies = []
        self._message_by_id = {}
        self._run_steps = []
        self._run_ok = True
        self._last_step = None
        return run_summary

    async def end_run(self, status: Optional[str] = None,
                       notes: Optional[str] = None) -> RunSummary:
        """End the active run (status inferred from whether every assertion
        passed, unless given) and return its RunSummary with step_results
        populated from this run's in-memory StepResults (diffs included)."""
        if self.run_id is None:
            raise ValueError("no active run to end")
        if status is None:
            status = "passed" if self._run_ok else "failed"
        run_summary = self.store.end_run(self.run_id, status, notes)
        run_summary.step_results = list(self._run_steps)
        run_summary.steps = len(self._run_steps)
        self.run_id = None
        return run_summary

    async def run_scenario(self, scenario: Union[Scenario, str, Path]) -> RunSummary:
        """Load (if needed), start a run for, execute every step of, and end
        a Scenario. Returns the RunSummary with all StepResults attached."""
        if isinstance(scenario, (str, Path)):
            scenario = load_scenario(scenario)
        await self.start_run(scenario.name, scenario.bot, scenario.source_path)
        for s in scenario.steps:
            action = s.action
            if s.timeout_ms is not None:
                action = action.model_copy(update={"timeout_ms": s.timeout_ms})
            await self.step(action, expect=s.expect, name=s.name)
        status = "passed" if self._run_ok else "failed"
        return await self.end_run(status=status)

    # ------------------------------------------------------------------
    # message tracking (for press_button target resolution)
    # ------------------------------------------------------------------

    def _track_messages(self, events: list[ObservationEvent]) -> None:
        for e in events:
            if e.message is None:
                continue
            self._message_by_id[e.message.message_id] = e.message
            if e.kind == "new" and e.message.from_bot:
                self.last_replies.append(e.message)
            elif e.kind == "edit":
                for i, m in enumerate(self.last_replies):
                    if m.message_id == e.message.message_id:
                        self.last_replies[i] = e.message
                        break
                else:
                    if e.message.from_bot:
                        self.last_replies.append(e.message)

    async def _resolve_button_target(self, action: Action) -> Optional[ObservedMessage]:
        """Which bot message's keyboard to press: `target_message_id` if
        given, else the most recent bot message with an inline keyboard
        (checking `self.last_replies` first, then `transport.last_bot_messages`),
        falling back to the most recent message with a reply keyboard."""
        if action.target_message_id is not None:
            cached = self._message_by_id.get(action.target_message_id)
            if cached is not None:
                return cached
            for m in await self.transport.last_bot_messages(20):
                if m.message_id == action.target_message_id:
                    return m
            return None

        for m in reversed(self.last_replies):
            if m.keyboard and m.keyboard.kind == "inline":
                return m
        fallback = list(reversed(await self.transport.last_bot_messages(10)))
        for m in fallback:
            if m.keyboard and m.keyboard.kind == "inline":
                return m
        for m in reversed(self.last_replies):
            if m.keyboard and m.keyboard.kind == "reply":
                return m
        for m in fallback:
            if m.keyboard and m.keyboard.kind == "reply":
                return m
        return None

    # ------------------------------------------------------------------
    # the core step
    # ------------------------------------------------------------------

    def _default_step_name(self, idx: int, action: Action) -> str:
        kind = action.kind.value if isinstance(action.kind, ActionKind) else str(action.kind)
        return f"{idx:02d}-{kind}-{_short_text(action)}"

    async def step(self, action: Action, expect: Optional[list[AssertionSpec]] = None,
                    name: Optional[str] = None) -> StepResult:
        """Act -> wait -> observe -> screenshot -> read -> store -> diff.

        Auto-starts an "adhoc" run if none is active. Always returns a fully
        populated StepResult: replies/edits/deletes derived from the events
        observed, a screenshot (or an explicit screenshot_error), a vision
        reading (or an explicit reason it is absent), evaluated assertions,
        a diff against the same-named step of the previous run of this
        scenario, and a human-readable `progress` line.
        """
        if self.run_id is None:
            await self.start_run("adhoc")

        expect = expect or []
        idx = self.step_idx
        step_name = name or self._default_step_name(idx, action)
        timeout_ms = action.timeout_ms or self.settings.reply_timeout_ms
        quiet_ms = self.settings.quiet_ms

        notes: list[str] = []
        sent_at = utcnow()
        events: list[ObservationEvent] = []

        try:
            # Late arrivals from a previous step must not be attributed to this
            # action. `wait` and `look` exist precisely to collect them.
            if action.kind not in (ActionKind.wait, ActionKind.look):
                stale = await self.transport.drain_events()
                if stale:
                    notes.append(f"discarded {len(stale)} late event(s) from before this step; "
                                 "use a `wait` step if they matter")
            if action.kind == ActionKind.send_text:
                if not action.text:
                    raise ValueError("send_text action requires text")
                await self.transport.send_text(action.text)
                events = await self.transport.wait_for_events(timeout_ms, quiet_ms)
            elif action.kind == ActionKind.send_command:
                if not action.text:
                    raise ValueError("send_command action requires text")
                await self.transport.send_text(action.text)
                events = await self.transport.wait_for_events(timeout_ms, quiet_ms)
            elif action.kind == ActionKind.send_file:
                if not action.file_path:
                    raise ValueError("send_file action requires file_path")
                await self.transport.send_file(action.file_path, action.caption)
                events = await self.transport.wait_for_events(timeout_ms, quiet_ms)
            elif action.kind == ActionKind.press_button:
                target = await self._resolve_button_target(action)
                if target is None:
                    notes.append("press_button: no message with a keyboard found")
                elif target.keyboard and target.keyboard.kind == "reply":
                    if not action.button_text:
                        raise ValueError("press_button on a reply keyboard requires button_text")
                    await self.transport.press_reply_button(action.button_text)
                    events = await self.transport.wait_for_events(timeout_ms, quiet_ms)
                else:
                    callback_answer = None
                    try:
                        callback_answer = await self.transport.press_inline(
                            target, text=action.button_text, index=action.button_index,
                            row=action.button_row, col=action.button_col,
                        )
                    except ValueError as e:
                        notes.append(f"press_button failed: {e}")
                    events = list(await self.transport.wait_for_events(timeout_ms, quiet_ms))
                    if callback_answer is not None:
                        events.append(ObservationEvent(kind="callback_answer", detail=callback_answer))
                    else:
                        notes.append("callback not answered by bot")
            elif action.kind == ActionKind.wait:
                events = await self.transport.wait_for_events(timeout_ms, quiet_ms)
            elif action.kind == ActionKind.look:
                events = await self.transport.drain_events()
            else:
                raise ValueError(f"unknown action kind: {action.kind!r}")
        except Exception as e:
            notes.append(f"action failed: {e}")

        done_at = utcnow()
        self._track_messages(events)

        replies = [e.message for e in events if e.kind == "new" and e.message and e.message.from_bot]
        edits = [e.message for e in events if e.kind == "edit" and e.message]
        deleted_ids: list[int] = []
        for e in events:
            if e.kind == "delete":
                deleted_ids.extend(e.message_ids)

        if events:
            timed_out = False
        elif action.kind in (ActionKind.look, ActionKind.wait) and not expect:
            timed_out = False
        else:
            timed_out = True
        if timed_out:
            notes.append(f"no reply within {timeout_ms}ms")

        step_id = self.store.add_step(self.run_id, idx, step_name, action, sent_at,
                                       done_at, timeout_ms, timed_out)
        for e in events:
            self.store.add_observation(step_id, e)

        # -- screenshot ---------------------------------------------------
        screenshot = None
        screenshot_error = None
        shot_id = None
        try:
            shot_path = Path(self.settings.shots) / self.run_id / f"{step_id}.png"
            screenshot = await self.web.screenshot(
                str(shot_path), region="last_messages", last_n=action.last_n or 5,
            )
            shot_id = self.store.add_screenshot(step_id, screenshot)
        except Exception as e:
            screenshot_error = str(e)
            if "disabled" in screenshot_error.lower():
                notes.append(f"visual eye disabled: {screenshot_error}")
            else:
                notes.append(f"screenshot failed: {screenshot_error}")

        # -- vision ---------------------------------------------------------
        vision: Optional[VisionReading] = None
        if screenshot is not None:
            expected = {
                "last_action": action.kind.value if isinstance(action.kind, ActionKind) else str(action.kind),
                "expected_text": [
                    str(s.value) for s in expect
                    if s.kind in ("text_contains", "text_equals", "text_regex")
                ],
                "expected_buttons": [str(s.value) for s in expect if s.kind == "has_inline_button"],
            }
            try:
                vision = await self.reader.read(screenshot, expected)
            except Exception as e:
                vision = VisionReading(provider=getattr(self._reader, "name", "unknown"), error=str(e))
            if shot_id is not None:
                self.store.add_vision(shot_id, vision)
            if vision.error:
                notes.append(f"reader error: {vision.error}")

        # -- assemble, evaluate, store assertions --------------------------
        step_result = StepResult(
            run_id=self.run_id, step_id=step_id, idx=idx, name=step_name, action=action,
            sent_at=sent_at, done_at=done_at, timed_out=timed_out, events=events,
            replies=replies, edits=edits, deleted_ids=deleted_ids,
            screenshot=screenshot, screenshot_error=screenshot_error, vision=vision,
            assertions=[], diff=None, progress="", notes=notes,
        )

        assertion_results = evaluate_all(expect, step_result)
        for spec, result in zip(expect, assertion_results):
            self.store.add_assertion(step_id, result, spec)
        step_result.assertions = assertion_results
        if not all(r.passed for r in assertion_results):
            self._run_ok = False

        # -- diff vs previous run of this scenario -------------------------
        prev_run = self.store.previous_run(self.scenario_name, self.run_id)
        prev_step = self.store.find_step(prev_run.run_id, step_name) if prev_run else None
        step_diff = diff_steps(step_result, prev_step)
        step_result.diff = step_diff
        step_result.progress = step_diff.summary

        self._run_steps.append(step_result)
        self._last_step = step_result
        self.step_idx += 1
        return step_result

    async def assert_last(self, specs: list[AssertionSpec]) -> list[AssertionResult]:
        """Evaluate extra assertions against the last StepResult, store them,
        append them to `last_step.assertions`, and return just the new results."""
        if self._last_step is None:
            raise ValueError("no step has run yet")
        results = evaluate_all(specs, self._last_step)
        for spec, result in zip(specs, results):
            self.store.add_assertion(self._last_step.step_id, result, spec)
        self._last_step.assertions = list(self._last_step.assertions) + results
        if not all(r.passed for r in results):
            self._run_ok = False
        return results

    async def submit_reading(self, description: str, issues: Optional[list[dict]] = None,
                             messages_seen: Optional[list[dict]] = None,
                             step_id: Optional[str] = None, model: Optional[str] = None) -> StepResult:
        """The calling LLM reports what it saw in a step's screenshot.

        Records a VisionReading(provider="caller") for the step (default: the
        last step), re-evaluates that step's vision_* assertions against it,
        updates the stored results, and returns the refreshed StepResult.
        Works with any provider, but is the whole point of provider="caller".
        """
        from graea.models import VisionFinding, VisionSeenMessage

        target = self._last_step
        if step_id is not None:
            target = next((s for s in self._run_steps if s.step_id == step_id), None)
            if target is None:
                raise ValueError(f"step {step_id!r} is not in the current run")
        if target is None:
            raise ValueError("no step to attach a reading to")

        prior_ocr = target.vision.ocr_text if target.vision else None
        reading = VisionReading(
            provider="caller", model=model, description=description,
            messages_seen=[VisionSeenMessage.model_validate(m) for m in (messages_seen or [])],
            issues=[VisionFinding.model_validate(i) for i in (issues or [])],
            ocr_text=prior_ocr, error=None,
        )
        shot_id = self.store.shot_id_for_step(target.step_id)
        if shot_id is not None:
            self.store.add_vision(shot_id, reading)
        target.vision = reading
        target.notes = [n for n in target.notes if not n.startswith("reader error: pending")]

        # re-evaluate the vision assertions that were pending
        stored = self.store.assertion_specs(target.step_id)
        refreshed: list[AssertionResult] = []
        by_name = {(a.name, a.kind): i for i, a in enumerate(target.assertions)}
        for assert_id, spec in stored:
            if not spec.kind.startswith("vision_"):
                continue
            result = evaluate_all([spec], target)[0]
            self.store.update_assertion(assert_id, result)
            key = (result.name, result.kind)
            if key in by_name:
                target.assertions[by_name[key]] = result
            refreshed.append(result)
        self._run_ok = all(a.passed for s in self._run_steps for a in s.assertions)
        if refreshed:
            target.notes.append(
                f"caller reading recorded; re-evaluated {len(refreshed)} vision assertion(s): "
                f"{sum(r.passed for r in refreshed)} passed"
            )
        else:
            target.notes.append("caller reading recorded")
        return target

    # ------------------------------------------------------------------
    # queries (sync — DuckDB is fast enough to call directly)
    # ------------------------------------------------------------------

    def diff(self, scenario: Optional[str] = None, run_a: Optional[str] = None,
              run_b: Optional[str] = None) -> DiffReport:
        """Diff two runs of a scenario. Defaults to the latest two ended
        runs of `scenario` (or the current/last-started run's scenario)."""
        if run_a is not None and run_b is not None:
            return diff_runs(self.store, run_a, run_b)

        scen = scenario or self.scenario_name
        if scen is None:
            raise ValueError("diff: no scenario given and none inferable from an active run")

        if run_b is None:
            candidates = [r for r in self.store.list_runs(scen, limit=50) if r.status != "running"]
            if not candidates:
                raise ValueError(f"diff: no ended runs of scenario {scen!r}")
            run_b = candidates[0].run_id
        if run_a is None:
            prev = self.store.previous_run(scen, run_b)
            if prev is None:
                raise ValueError(f"diff: no previous run of scenario {scen!r} to compare against {run_b}")
            run_a = prev.run_id
        return diff_runs(self.store, run_a, run_b)

    def history(self, scenario: str, assertion: Optional[str] = None,
                limit: int = 50) -> list[dict]:
        """Assertion-history rows for a scenario (optionally filtered to one
        assertion name), newest first. See Store.assertion_history."""
        return self.store.assertion_history(scenario, assertion, limit)

    def sql(self, query: str) -> list[dict]:
        """Run a read-only SELECT/WITH query against the store. See Store.sql."""
        return self.store.sql(query)
