# interfaces agent report

Owns: `graea/interfaces/mcp_server.py`, `graea/interfaces/cli.py`,
`graea/interfaces/http.py`, `tests/test_interfaces.py`, plus
`tests/conftest.py` (shared `FakeSession` fixture, not owned by any other
agent — created fresh, no conflicts found).

## Files written

- `graea/interfaces/mcp_server.py` — FastMCP stdio server, `mcp = FastMCP("graea")`, 14 tools.
- `graea/interfaces/cli.py` — typer `app`.
- `graea/interfaces/http.py` — FastAPI `app` + `serve(settings)`.
- `tests/conftest.py` — `FakeSession` (implements the full `TestSession` API
  given in the task) + fixtures `fake_settings`, `fake_session`,
  `patched_interfaces` (monkeypatches all three interfaces' session factories).
- `tests/test_interfaces.py` — 31 tests.

`python -m pytest tests/test_interfaces.py -q` → **31 passed**.
Full suite `python -m pytest -q` → **168 passed** (runner.py landed from the
concurrent engine agent partway through this work; no changes needed on my
side once it appeared — I coded directly against the given API signature the
whole time).

## Session wiring (how to swap the fake in)

Each of the three modules exposes a module-level `_session_factory` that
production code never needs to touch:

- `mcp_server._session_factory: Callable[[], Awaitable[TestSession]]` — zero-arg
  async factory; default builds `Settings()` + `TestSession(settings)` +
  `await session.start()`. The session itself is memoized in module global
  `_session` (lazy, first-tool-call-wins). Tests monkeypatch
  `_session_factory` to an async no-arg callable returning the already-started
  `FakeSession`, and reset `_session = None` first (since it's a
  process-global cache).
- `cli._session_factory: Callable[[Settings], TestSession]` and
  `cli._get_settings: Callable[[], Settings]` — every CLI command calls
  `_with_session()` which builds settings, builds+starts a fresh session,
  runs the command body, then stops the session. Tests monkeypatch both.
- `http._session_factory` / `http._get_settings` — same shape, but the
  session is built once in the FastAPI `lifespan` context manager and stashed
  on `app.state.session` (+ `app.state.settings`) for the process's life;
  `TestClient(app)` must be used as a context manager (`with TestClient(app)
  as client:`) to trigger the lifespan startup/shutdown.

## MCP tool surface — verified against installed `mcp` 1.27.0

All 14 tools from SPEC.md are registered under `mcp._tool_manager`, named
exactly `graea_start_run`, `graea_send`, `graea_command`,
`graea_press_button`, `graea_send_file`, `graea_wait`,
`graea_look`, `graea_assert`, `graea_run_scenario`, `graea_diff`,
`graea_history`, `graea_sql`, `graea_end_run`, `graea_status`.

**Return shape, confirmed by reading `mcp.server.fastmcp.tools.base.Tool.run`
and `utilities.func_metadata.FuncMetadata.convert_result` in the installed
package**: a tool function may return `list[TextContent | Image | ...]`
directly. `Tool.run(..., convert_result=False)` (what `_tool_manager.get_tool(name).run(args)`
gives you, and what the tests call) hands back that list completely
unconverted — `TextContent` instances pass straight through and `Image`
instances stay `Image` objects (call `.to_image_content()` yourself to get an
`ImageContent`, which the tests do to verify base64 PNG data + `mimeType`).
`convert_result=True` (what the real MCP protocol path uses) runs the list
through `_convert_to_content`, which is `Image`'s whole purpose — it's the
documented FastMCP helper for exactly this "screenshot alongside text" case,
so no custom conversion code was needed. `TextContent`/`ImageContent` both
come from `mcp.types` and require `type="text"`/`type="image"` set
explicitly (not defaulted) in this version.

Tools that touch a screenshot return `[TextContent(json), Image(path=...)]`
(image omitted only if `step.screenshot` is `None`). `graea_start_run`,
`graea_end_run`, `graea_assert`, `graea_diff`, `graea_history`,
`graea_sql`, `graea_status` are text-only (no screenshot in play).
`graea_run_scenario` returns `[TextContent(compact RunSummary)] +
[Image(...) for each failing step's screenshot, capped at 6]` — passing
steps' screenshots are never attached (their paths are still in the JSON if
the caller wants to look), tested directly (`test_run_scenario_caps_failing_images`,
`test_run_scenario_only_failing_get_images`).

Compactness: every `StepResult`/`RunSummary` JSON is built via
`model_dump_json(indent=2, exclude=...)` with `ObservedMessage.raw` always
excluded and `ObservedMessage.entities` additionally excluded unless a
`verbose=True` path is used (helper `_msg_exclude`/`_step_exclude` in
`mcp_server.py`; a smaller inline version — `raw` only — in `cli.py`'s ad hoc
`step` printer). Note: I did not expose a `verbose` parameter on the MCP
tools themselves (SPEC's tool signatures don't list one) — the plumbing
(`_step_json(step, verbose=...)`) is there and trivial to wire to a parameter
later if wanted; today `verbose` is effectively always `False` from the tool
call sites.

`graea_sql` never raises: `session.sql(query)` is wrapped in
try/except and any exception (rejected non-SELECT, DuckDB error) becomes
`{"error": "<message>"}` as `TextContent`, verified with `DROP TABLE runs`.

## CLI

`graea run <scenario> [--bot] [--source] [--json]`: loads the scenario via
`load_scenario`, overrides `.bot`/`.source_path` on the `Scenario` object
in-memory when `--bot`/`--source` are passed (the underlying
`run_scenario(scenario: Scenario|str|Path)` API has no bot/source override
params, so this was the natural way to support the CLI flags described in
the task without touching the engine's contract). Prints a rich table (idx,
name, assertions pass/total, vision issue count, per-step `progress`) unless
`--json`, and exits 1 when `summary.status == "failed"`. `graea runs
[--scenario]` has no direct `TestSession` method for listing runs, so it's
implemented as `session.sql("SELECT run_id, scenario, bot_username, status,
started_at, ended_at FROM runs ...")` — legal since `sql()` is the one
documented read escape hatch and matches the `runs` table schema in
SPEC.md/store.py. Falls back to plain `print`s if `rich` isn't importable
(checked via `try/except ImportError` at module load, not exercised by tests
since `rich` is installed here).

`graea login` drives `TelethonTransport.login_interactive(code_callback=,
password_callback=)` with `typer.prompt` callbacks; `graea login-web`
starts `TelegramWeb(settings)` (headless unless `--headed`), and if
`is_logged_in()` is false, saves the QR to `data/login-qr.png` (per spec
literal path), prints instructions, then calls
`web.wait_for_login(timeout_s=...)` once (`--timeout`, default 180) — no
polling loop needed since `wait_for_login` is documented (visual agent's
report) to already poll internally up to its timeout.

## HTTP

Routes match SPEC.md exactly: `POST /run/start`, `/run/end`, `/step/send`,
`/step/command`, `/step/press`, `/step/file`, `/step/wait`, `/step/look`,
`/assert`, `/scenario/run`, `GET /diff`, `/history`, `POST /sql`, `GET
/status`, `GET /shots/{run_id}/{file}`. Every step/scenario body accepts an
optional `expect: list[AssertionSpec]` and `name: str` (pydantic validates
`AssertionSpec` directly, so a bad assertion kind is a 422 from FastAPI, not
a 500). `/sql` always returns HTTP 200, with `{"error": ...}` on any
exception (tested with `DROP TABLE runs`) — matches the "propagate, don't
crash" rule. `/shots/{run_id}/{file}` serves
`settings.shots/<run_id>/<file>` as `image/png`, 404 if missing (this
run_id-subdirectory layout is an assumption on my part — no other agent's
report documents where the engine actually writes screenshot files day to
day, so this may need to move to match wherever `runner.py` actually places
them; the *screenshot path already embedded in every StepResult JSON* is the
authoritative way to find a shot regardless of this route's layout).

## Known gaps / notes for other agents

- `runner.py` did not exist when this work started (per the task); I coded
  entirely against the given method signatures and never imported anything
  from it beyond `TestSession`/the documented API. Once it appeared,
  `python -m pytest -q` (full suite) passed with zero changes needed on my
  side — the constructor `TestSession(settings, transport=None, web=None,
  reader=None, store=None)` and every awaited method matched.
- `FakeSession` (in `tests/conftest.py`) writes a real 320x240 PNG per step
  via Pillow to `tmp_path/shots/step<N>.png` (fresh file per step, reused
  across a test's steps) and computes real sha256 — `test_send_image_content_converts`
  round-trips it through `Image.to_image_content()` to confirm real base64
  PNG bytes flow all the way through.
- No `verbose=True` path is currently reachable from any tool's public
  parameters (see above) — flagging in case another agent/spec review wants
  it added as an explicit MCP/HTTP parameter later; the exclude-set plumbing
  already supports it.
- `/shots/{run_id}/{file}` directory convention is a guess (see above); low
  risk since it's not required for the LLM tool-calling path (which gets
  screenshots as inline MCP image blocks, not via this URL), but the HTTP
  caller relying on this route for images should double check the real
  layout `runner.py`/`store.py` write to.
- Did not touch `graea/models.py`, `config.py`, `client/protocol.py`, or
  any other agent's files. `docs/CONTRACT-NOTES.md` needed no additions —
  the given `TestSession` signature was sufficient to build against.
