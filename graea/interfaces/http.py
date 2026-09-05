"""FastAPI wrapper exposing the same operations as the MCP tool surface over HTTP.

One `TestSession` per process, created on app startup (FastAPI lifespan) and
stopped on shutdown. Screenshots are not embedded in JSON responses — fetch
them from `GET /shots/{run_id}/{file}` using the file name in
`StepResult.screenshot.path`.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import json
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from graea.config import Settings, get_settings
from graea.engine.runner import TestSession
from graea.engine.scenario import load_scenario
from graea.models import Action, ActionKind, AssertionSpec

# Swapped out by tests. Production default: the real TestSession.
_session_factory: Callable[[Settings], Any] = TestSession
_get_settings: Callable[[], Settings] = get_settings


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = _get_settings()
    settings.ensure_dirs()
    session = _session_factory(settings)
    await session.start()
    app.state.session = session
    app.state.settings = settings
    try:
        yield
    finally:
        await session.stop()


app = FastAPI(title="graea", description="HTTP interface over TestSession", lifespan=_lifespan)


def _session(request: Request) -> TestSession:
    return request.app.state.session


_STEP_EXCLUDE = {
    "replies": {"__all__": {"raw"}},
    "edits": {"__all__": {"raw"}},
    "events": {"__all__": {"message": {"raw"}}},
}


def _step_response(step) -> JSONResponse:
    return JSONResponse(step.model_dump(mode="json", exclude=_STEP_EXCLUDE))


def _run_response(run) -> JSONResponse:
    return JSONResponse(run.model_dump(mode="json", exclude={"step_results": {"__all__": _STEP_EXCLUDE}}))


# --------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------


class StartRunRequest(BaseModel):
    scenario: str = "adhoc"
    bot: Optional[str] = None
    source_path: Optional[str] = None
    notes: Optional[str] = None


class EndRunRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


class SendRequest(BaseModel):
    text: str
    expect: Optional[list[AssertionSpec]] = None
    name: Optional[str] = None


class CommandRequest(BaseModel):
    command: str
    expect: Optional[list[AssertionSpec]] = None
    name: Optional[str] = None


class PressRequest(BaseModel):
    text: Optional[str] = None
    index: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None
    expect: Optional[list[AssertionSpec]] = None
    name: Optional[str] = None


class FileRequest(BaseModel):
    path: str
    caption: Optional[str] = None
    expect: Optional[list[AssertionSpec]] = None
    name: Optional[str] = None


class WaitRequest(BaseModel):
    timeout_ms: int = 8000
    expect: Optional[list[AssertionSpec]] = None
    name: Optional[str] = None


class LookRequest(BaseModel):
    last_n: int = 5
    annotate: bool = False
    expect: Optional[list[AssertionSpec]] = None
    name: Optional[str] = None


class AssertRequest(BaseModel):
    assertions: list[AssertionSpec]


class ReadingRequest(BaseModel):
    description: str
    issues: list[dict] = []
    messages_seen: list[dict] = []
    step_id: Optional[str] = None
    model: Optional[str] = None


class ScenarioRunRequest(BaseModel):
    path_or_name: str


class SqlRequest(BaseModel):
    query: str


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.post("/run/start")
async def run_start(body: StartRunRequest, request: Request) -> JSONResponse:
    """Start (or restart) a run. Returns the new RunSummary."""
    run = await _session(request).start_run(scenario=body.scenario, bot=body.bot,
                                              source_path=body.source_path, notes=body.notes)
    return _run_response(run)


@app.post("/run/end")
async def run_end(body: EndRunRequest, request: Request) -> JSONResponse:
    """End the current run. Returns the final RunSummary."""
    run = await _session(request).end_run(status=body.status, notes=body.notes)
    return _run_response(run)


@app.post("/step/send")
async def step_send(body: SendRequest, request: Request) -> JSONResponse:
    """Send plain text to the bot; returns the StepResult."""
    step = await _session(request).step(Action(kind=ActionKind.send_text, text=body.text),
                                         expect=body.expect, name=body.name)
    return _step_response(step)


@app.post("/step/command")
async def step_command(body: CommandRequest, request: Request) -> JSONResponse:
    """Send a bot command (e.g. "/start"); returns the StepResult."""
    step = await _session(request).step(Action(kind=ActionKind.send_command, text=body.command),
                                         expect=body.expect, name=body.name)
    return _step_response(step)


@app.post("/step/press")
async def step_press(body: PressRequest, request: Request) -> JSONResponse:
    """Press an inline button by text, flat index, or row+col; returns the StepResult."""
    action = Action(kind=ActionKind.press_button, button_text=body.text, button_index=body.index,
                     button_row=body.row, button_col=body.col)
    step = await _session(request).step(action, expect=body.expect, name=body.name)
    return _step_response(step)


@app.post("/step/file")
async def step_file(body: FileRequest, request: Request) -> JSONResponse:
    """Send a local file (with optional caption); returns the StepResult."""
    action = Action(kind=ActionKind.send_file, file_path=body.path, caption=body.caption)
    step = await _session(request).step(action, expect=body.expect, name=body.name)
    return _step_response(step)


@app.post("/step/wait")
async def step_wait(body: WaitRequest, request: Request) -> JSONResponse:
    """Observe without acting for up to timeout_ms; returns the StepResult."""
    step = await _session(request).step(Action(kind=ActionKind.wait, timeout_ms=body.timeout_ms),
                                         expect=body.expect, name=body.name)
    return _step_response(step)


@app.post("/step/look")
async def step_look(body: LookRequest, request: Request) -> JSONResponse:
    """Take a fresh screenshot + vision reading without acting; returns the StepResult."""
    action = Action(kind=ActionKind.look, last_n=body.last_n)
    step = await _session(request).step(action, expect=body.expect, name=body.name)
    return _step_response(step)


@app.post("/assert")
async def assert_last(body: AssertRequest, request: Request) -> JSONResponse:
    """Evaluate assertions against the most recent step. Returns a list of AssertionResult."""
    results = await _session(request).assert_last(body.assertions)
    return JSONResponse([r.model_dump(mode="json") for r in results])


@app.post("/reading")
async def submit_reading(body: ReadingRequest, request: Request) -> JSONResponse:
    """The caller reports what it saw in a step's screenshot (provider=caller). Returns the refreshed StepResult."""
    step = await _session(request).submit_reading(
        body.description, issues=body.issues, messages_seen=body.messages_seen,
        step_id=body.step_id, model=body.model,
    )
    return JSONResponse(json.loads(step.model_dump_json(exclude={"replies": {"__all__": {"raw"}}})))


@app.post("/scenario/run")
async def scenario_run(body: ScenarioRunRequest, request: Request) -> JSONResponse:
    """Run a whole YAML scenario end-to-end. Returns the RunSummary (all step results)."""
    scenario = load_scenario(body.path_or_name)
    run = await _session(request).run_scenario(scenario)
    return _run_response(run)


@app.get("/diff")
async def diff(request: Request, scenario: Optional[str] = None,
                run_a: Optional[str] = None, run_b: Optional[str] = None) -> JSONResponse:
    """Diff two runs of a scenario. Returns a DiffReport."""
    report = _session(request).diff(scenario=scenario, run_a=run_a, run_b=run_b)
    return JSONResponse(report.model_dump(mode="json"))


@app.get("/history")
async def history(request: Request, scenario: str, assertion: Optional[str] = None,
                   limit: int = 50) -> JSONResponse:
    """Assertion pass/fail history across past runs of a scenario."""
    rows = _session(request).history(scenario, assertion=assertion, limit=limit)
    return JSONResponse(rows)


@app.post("/sql")
async def sql(body: SqlRequest, request: Request) -> JSONResponse:
    """Run a read-only SELECT against the DuckDB store. Errors come back as {"error": ...}, HTTP 200."""
    try:
        rows = _session(request).sql(body.query)
    except Exception as exc:
        return JSONResponse({"error": str(exc)})
    return JSONResponse(rows)


@app.get("/status")
async def status(request: Request) -> JSONResponse:
    """Engine health: MTProto connection, web login, vision provider, DB path, active run."""
    health = await _session(request).health()
    return JSONResponse(health.model_dump(mode="json"))


@app.get("/shots/{run_id}/{file}")
async def shots(run_id: str, file: str, request: Request) -> FileResponse:
    """Serve a screenshot PNG saved under settings.shots for a given run."""
    settings: Settings = request.app.state.settings
    path = Path(settings.shots) / run_id / file
    if not path.is_file():
        raise HTTPException(status_code=404, detail="screenshot not found")
    return FileResponse(str(path), media_type="image/png")


def serve(settings: Optional[Settings] = None) -> None:
    """Run the HTTP interface with uvicorn (blocking)."""
    settings = settings or _get_settings()
    uvicorn.run(app, host=settings.http_host, port=settings.http_port)
