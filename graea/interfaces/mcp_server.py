"""FastMCP server exposing Graea to an LLM over stdio.

Every tool here talks to a single, lazily-created `TestSession` (see
`graea.engine.runner`). The session is started on first use and kept alive
for the life of the process; call `graea_status` any time to check its
health, and `graea_start_run` to (re)start a run before driving steps.

Tools that produce a screenshot (send/command/press_button/send_file/wait/look,
and the failing steps of run_scenario) return **both** a compact JSON text
block describing the step and an MCP image block of the screenshot, so a
vision-capable LLM caller can look at the same screenshot the built-in vision
reader already described in the JSON. A non-vision caller should read
`vision.description` / `vision.issues` in the JSON instead.

Run `python -m graea.interfaces.mcp_server` (or the `graea mcp` CLI
command) to serve this over stdio.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import TextContent

from graea.config import get_settings
from graea.engine.runner import TestSession
from graea.engine.scenario import load_scenario
from graea.models import Action, ActionKind, AssertionSpec, UpdateInfo
from graea.version import __version__, check_for_update

mcp = FastMCP("graea")

# Swapped out by tests to inject a fake session builder; production default
# is the real TestSession constructor.
_session_factory: Callable[[], Any] = None  # set below, after TestSession import
_session: Optional[TestSession] = None

# Cached once per process, at session start, so per-step tool calls never hit
# the network (check_for_update itself is a 24h on-disk cache too).
_cached_update: Optional[UpdateInfo] = None


async def _default_session_factory() -> TestSession:
    settings = get_settings()
    settings.ensure_dirs()
    session = TestSession(settings)
    await session.start()
    return session


_session_factory = _default_session_factory


async def get_session() -> TestSession:
    """Return the process-wide TestSession, starting it lazily on first call.

    Also runs (and caches for the process's lifetime) the update check exactly
    once here, so later per-step tool calls can append an update note without
    ever touching the network themselves.
    """
    global _session, _cached_update
    if _session is None:
        _session = await _session_factory()
        try:
            _cached_update = check_for_update(get_settings())
        except Exception:
            _cached_update = None
    return _session


# --------------------------------------------------------------------------
# Compact JSON helpers — keep tool output small; screenshots ride separately
# as MCP image blocks, and raw telethon payloads / entity ranges are noise
# for an LLM unless it explicitly asks for verbose=True.
# --------------------------------------------------------------------------


def _msg_exclude(verbose: bool) -> set[str]:
    fields = {"raw"}
    if not verbose:
        fields.add("entities")
    return fields


def _step_exclude(verbose: bool) -> dict[str, Any]:
    me = _msg_exclude(verbose)
    return {
        "replies": {"__all__": me},
        "edits": {"__all__": me},
        "events": {"__all__": {"message": me}},
    }


def _step_json(step, verbose: bool = False) -> str:
    return step.model_dump_json(indent=2, exclude=_step_exclude(verbose))


def _run_summary_json(run, verbose: bool = False) -> str:
    exclude = {"step_results": {"__all__": _step_exclude(verbose)}}
    return run.model_dump_json(indent=2, exclude=exclude)


def _step_content(step, verbose: bool = False) -> list:
    """[TextContent(compact StepResult JSON), Image(screenshot)] — image omitted if none."""
    if _cached_update is not None and _cached_update.update_available:
        step.notes = [
            *step.notes,
            f"update available: {_cached_update.current} -> {_cached_update.latest} (run: graea update)",
        ]
    content: list = [TextContent(type="text", text=_step_json(step, verbose))]
    if step.screenshot is not None:
        content.append(Image(path=step.screenshot.path))
    return content


def _text(obj_json: str) -> list:
    return [TextContent(type="text", text=obj_json)]


# --------------------------------------------------------------------------
# Run lifecycle
# --------------------------------------------------------------------------


@mcp.tool()
async def graea_start_run(scenario: str = "adhoc", bot: Optional[str] = None,
                              source_path: Optional[str] = None, notes: Optional[str] = None) -> list:
    """Start (or restart) a test run against the bot.

    Call this before any `graea_send`/`graea_command`/... unless you are
    happy with the engine auto-starting an "adhoc" run for you. `scenario` is
    a free-form label used for history/diffing (use the real scenario name if
    you intend to later call `graea_run_scenario` with the same name so
    runs compare against each other). `source_path`, if given, lets the
    engine fingerprint the bot's source tree so regressions can be pinned to
    a code change. Returns the new RunSummary as JSON text — check `run_id`
    and `progress` (compares against the previous run of this scenario, if
    any).
    """
    session = await get_session()
    run = await session.start_run(scenario=scenario, bot=bot, source_path=source_path, notes=notes)
    return _text(_run_summary_json(run))


@mcp.tool()
async def graea_end_run(status: Optional[str] = None, notes: Optional[str] = None) -> list:
    """End the current run (status: "passed"/"failed"/"aborted"; inferred if omitted).

    Call this when you're done driving the bot for this run so it gets an
    `ended_at` and becomes eligible as the "previous run" future runs diff
    against. Returns the final RunSummary as JSON text, including
    `assertions_total`/`assertions_passed` and `progress`.
    """
    session = await get_session()
    run = await session.end_run(status=status, notes=notes)
    return _text(_run_summary_json(run))


# --------------------------------------------------------------------------
# Steps (act -> wait -> observe -> screenshot -> read -> store -> diff)
# --------------------------------------------------------------------------


def _specs(expect: Optional[list[dict]]) -> Optional[list[AssertionSpec]]:
    if not expect:
        return None
    return [AssertionSpec.model_validate(e) for e in expect]


@mcp.tool()
async def graea_send(text: str, expect: Optional[list[dict]] = None,
                         name: Optional[str] = None) -> list:
    """Send a plain text message to the bot as the test user, then observe its reply.

    `expect` is an optional list of assertion specs (see `graea_assert`)
    evaluated against this step's result in the same call. Returns a compact
    StepResult as JSON text plus a screenshot image of the chat after the
    reply settled. Check `replies` (new bot messages), `vision.description`/
    `vision.issues` (what a human would actually see), `assertions`, and
    `progress` (how this compares to the previous run of the current
    scenario) first.
    """
    session = await get_session()
    step = await session.step(Action(kind=ActionKind.send_text, text=text), expect=_specs(expect), name=name)
    return _step_content(step)


@mcp.tool()
async def graea_command(command: str, expect: Optional[list[dict]] = None,
                            name: Optional[str] = None) -> list:
    """Send a bot command (e.g. "/start") as the test user, then observe its reply.

    Same return shape as `graea_send`: compact StepResult JSON + a
    screenshot image. Use this instead of `graea_send` for anything
    starting with "/" so the engine and history can distinguish commands
    from ordinary text.
    """
    session = await get_session()
    step = await session.step(Action(kind=ActionKind.send_command, text=command), expect=_specs(expect), name=name)
    return _step_content(step)


@mcp.tool()
async def graea_press_button(text: Optional[str] = None, index: Optional[int] = None,
                                 row: Optional[int] = None, col: Optional[int] = None,
                                 expect: Optional[list[dict]] = None, name: Optional[str] = None) -> list:
    """Press an inline keyboard button on the most recent bot message.

    Identify the button by exactly one of: `text` (label match), `index`
    (flat position across all rows), or `row`+`col`. Returns compact
    StepResult JSON + screenshot. If the bot never answers the callback
    (a "dead button" bug), `assertions`/`vision.issues` will reflect a
    spinner/no-response rather than the tool raising — check `timed_out` and
    `notes` too.
    """
    session = await get_session()
    action = Action(kind=ActionKind.press_button, button_text=text, button_index=index,
                     button_row=row, button_col=col)
    step = await session.step(action, expect=_specs(expect), name=name)
    return _step_content(step)


@mcp.tool()
async def graea_send_file(path: str, caption: Optional[str] = None,
                              expect: Optional[list[dict]] = None, name: Optional[str] = None) -> list:
    """Send a local file (photo/document/etc.) to the bot as the test user, with an optional caption.

    `path` must be a file readable on this machine. Returns compact
    StepResult JSON + a screenshot of the resulting chat state.
    """
    session = await get_session()
    action = Action(kind=ActionKind.send_file, file_path=path, caption=caption)
    step = await session.step(action, expect=_specs(expect), name=name)
    return _step_content(step)


@mcp.tool()
async def graea_wait(timeout_ms: int = 8000, expect: Optional[list[dict]] = None,
                         name: Optional[str] = None) -> list:
    """Observe the chat for up to `timeout_ms` without taking any action.

    Useful for catching delayed replies, edits, or silence (a bot that
    should answer but doesn't). Returns compact StepResult JSON + screenshot;
    `timed_out=true` and empty `replies` mean nothing arrived.
    """
    session = await get_session()
    step = await session.step(Action(kind=ActionKind.wait, timeout_ms=timeout_ms), expect=_specs(expect), name=name)
    return _step_content(step)


@mcp.tool()
async def graea_look(last_n: int = 5, annotate: bool = False,
                         expect: Optional[list[dict]] = None, name: Optional[str] = None) -> list:
    """Take a fresh screenshot of the last `last_n` messages and read it, without acting.

    Use this to double-check what the chat currently looks like (e.g. after
    an external event, or before asserting) without sending anything.
    Returns compact StepResult JSON + screenshot. `annotate` (if supported by
    the visual eye) asks for bounding boxes over the vision findings.
    """
    session = await get_session()
    action = Action(kind=ActionKind.look, last_n=last_n)
    step = await session.step(action, expect=_specs(expect), name=name)
    return _step_content(step)


@mcp.tool()
async def graea_assert(assertions: list[dict]) -> list:
    """Evaluate assertions against the most recent step's result (no new action taken).

    Each item is an assertion spec dict, e.g.
    `{"kind": "text_contains", "value": "Welcome"}` or
    `{"kind": "reply_count", "op": ">=", "value": 1}`. See SPEC.md
    "Assertion kinds" for the full list. Returns a JSON list of
    AssertionResult (`name`, `kind`, `passed`, `actual`, `message`) — read
    `message` on any failure for why.
    """
    session = await get_session()
    specs = [AssertionSpec.model_validate(a) for a in assertions]
    results = await session.assert_last(specs)
    return _text(json.dumps([r.model_dump(mode="json") for r in results], indent=2))


@mcp.tool()
async def graea_submit_reading(description: str, issues: Optional[list[dict]] = None,
                               messages_seen: Optional[list[dict]] = None,
                               step_id: Optional[str] = None, model: Optional[str] = None) -> list:
    """You are the reader: report what you saw in the last step's screenshot.

    With GRAEA_VISION_PROVIDER=caller (the default) no separate vision model
    runs; every step returns its screenshot as an image and `vision.error`
    says "pending". Look at the image and call this with a plain
    `description` of the chat (newest message last) and `issues`, a list of
    `{"severity": "low|medium|high", "kind": "raw_markdown|truncated_button|
    missing_caption|empty_message|broken_media|layout|mismatch|other",
    "detail": "..."}` (empty list = nothing wrong). Optionally
    `messages_seen`: `[{"sender": "bot|user", "text_as_rendered": "...",
    "buttons": ["..."]}]`. The reading is stored, the step's vision_*
    assertions are re-evaluated, and the refreshed StepResult comes back.
    Default target is the last step; pass `step_id` for an earlier one in
    the current run.
    """
    session = await get_session()
    step = await session.submit_reading(description, issues=issues, messages_seen=messages_seen,
                                        step_id=step_id, model=model)
    return _text(_step_json(step))


# --------------------------------------------------------------------------
# Scenarios / history / diff / sql
# --------------------------------------------------------------------------

_MAX_FAILING_IMAGES = 6


@mcp.tool()
async def graea_run_scenario(path_or_name: str) -> list:
    """Run a whole YAML scenario end-to-end (its own steps, its own expectations).

    `path_or_name` is either a path to a scenario YAML file or a bare name
    resolved against the bundled demo scenarios directory. Returns a compact
    RunSummary as JSON text (every step's result, but without screenshots
    inline) followed by screenshot images of only the **failing** steps
    (capped at 6 images so the response stays small) — passing steps'
    screenshots are in `step_results[].screenshot.path` on disk if you need
    them. Check `status`, `progress`, and each failing step's
    `vision.description`/`assertions` first.
    """
    session = await get_session()
    scenario = load_scenario(path_or_name)
    run = await session.run_scenario(scenario)
    content: list = [TextContent(type="text", text=_run_summary_json(run))]
    failing = [s for s in run.step_results if not s.passed]
    for step in failing[:_MAX_FAILING_IMAGES]:
        if step.screenshot is not None:
            content.append(Image(path=step.screenshot.path))
    return content


@mcp.tool()
async def graea_diff(scenario: Optional[str] = None, run_a: Optional[str] = None,
                         run_b: Optional[str] = None) -> list:
    """Diff two runs of a scenario (defaults: the current scenario's last two ended runs).

    Pass explicit `run_a`/`run_b` run ids to compare specific runs. Returns a
    DiffReport as JSON text: `fixed`/`regressed`/`still_failing`/
    `still_passing`/`new_assertions` (each entry `"<step name>/<assertion
    name>"`), plus `missing_steps` and a one-line `summary`.
    """
    session = await get_session()
    report = session.diff(scenario=scenario, run_a=run_a, run_b=run_b)
    return _text(report.model_dump_json(indent=2))


@mcp.tool()
async def graea_history(scenario: str, assertion: Optional[str] = None, limit: int = 50) -> list:
    """Look up an assertion's pass/fail history across past runs of a scenario.

    Omit `assertion` to get every assertion's history for the scenario.
    Returns a JSON list of rows: `run_id`, `fingerprint`, `started_at`,
    `step_name`, `assertion`, `passed`, `actual` — newest first, useful for
    "did this ever pass" / "when did this start failing" questions.
    """
    session = await get_session()
    rows = session.history(scenario, assertion=assertion, limit=limit)
    return _text(json.dumps(rows, indent=2, default=str))


@mcp.tool()
async def graea_sql(query: str) -> list:
    """Run a read-only SELECT against the Graea DuckDB store (runs/steps/observations/screenshots/vision_readings/assertions).

    Only a single SELECT or WITH statement is allowed; anything else is
    rejected. On rejection or any query error, returns
    `{"error": "<message>"}` as JSON text rather than raising — check for an
    "error" key before trusting the result. See docs/SPEC.md for the table
    schema.
    """
    session = await get_session()
    try:
        rows = session.sql(query)
    except Exception as exc:  # never crash the tool on a bad/rejected query
        return _text(json.dumps({"error": str(exc)}))
    return _text(json.dumps(rows, indent=2, default=str))


@mcp.tool()
async def graea_status() -> list:
    """Report engine health: MTProto connection, Telegram Web login, vision provider, DB path, active run.

    Call this first if anything else is behaving unexpectedly — a `None`
    `mtproto_user` or `web_logged_in=false` means the two eyes aren't set up
    yet (run `graea login` / `graea login-web`), and `vision_ok=false`
    means the reader is misconfigured.
    """
    session = await get_session()
    health = await session.health()
    health.version = __version__
    health.update = _cached_update
    return _text(health.model_dump_json(indent=2))


@mcp.tool()
async def graea_update_check() -> list:
    """Force a check for a newer graea release (bypasses the 24h cache) and return it.

    Unlike the update info folded into `graea_status`/every step's `notes`
    (which is read from the once-per-session cached check), this always
    hits GitHub (bounded to 3s) — use it when you want a fresh answer, e.g.
    right after telling the user you're about to check. Returns an
    UpdateInfo JSON: `current`, `latest`, `update_available`, `html_url`,
    `how_to_update` (commands for this install), `checked_at`. Never raises;
    if the check is disabled (`GRAEA_CHECK_UPDATES=false`), offline
    (`GRAEA_OFFLINE=1`), or GitHub is unreachable, returns
    `{"update_available": false, "note": "..."}` instead.
    """
    global _cached_update
    await get_session()
    settings = get_settings()
    info = check_for_update(settings, force=True)
    _cached_update = info
    if info is None:
        return _text(json.dumps({
            "update_available": False,
            "note": "check disabled, offline, or the GitHub check failed/timed out",
        }))
    return _text(info.model_dump_json(indent=2))


def main() -> None:
    """Entry point: serve this MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
