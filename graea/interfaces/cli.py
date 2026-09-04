"""`graea` typer CLI — login, run scenarios, ad hoc steps, history/diff/sql, and
launching the HTTP/MCP interfaces.

Every command builds its own `Settings`, constructs a session for the
duration of the command (`asyncio.run`), and tears it down again — no
long-lived process state between invocations (use `graea serve` or
`graea mcp` for a persistent session).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Optional

import typer

from graea.config import Settings, get_settings
from graea.engine.runner import TestSession
from graea.engine.scenario import load_scenario
from graea.models import Action, ActionKind

try:
    from rich.console import Console
    from rich.table import Table
    _console: Optional["Console"] = Console()
except ImportError:  # pragma: no cover - exercised only when rich is absent
    _console = None

app = typer.Typer(name="graea", help="Give an LLM eyes on a Telegram bot.")
step_app = typer.Typer(help="Ad hoc single steps against the current/adhoc run.")
app.add_typer(step_app, name="step")

# Swapped out by tests. Production default: the real TestSession.
_session_factory: Callable[[Settings], Any] = TestSession
_get_settings: Callable[[], Settings] = get_settings


def _print(msg: str) -> None:
    if _console is not None:
        _console.print(msg)
    else:  # pragma: no cover
        print(msg)


def _table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    if _console is not None:
        t = Table(title=title)
        for c in columns:
            t.add_column(c)
        for r in rows:
            t.add_row(*[str(x) for x in r])
        _console.print(t)
    else:  # pragma: no cover
        print(title)
        print(" | ".join(columns))
        for r in rows:
            print(" | ".join(str(x) for x in r))


async def _with_session(fn):
    """Build settings + a session, run `fn(session)`, always stop the session after."""
    settings = _get_settings()
    settings.ensure_dirs()
    session = _session_factory(settings)
    await session.start()
    try:
        return await fn(session)
    finally:
        await session.stop()


def _run(coro_fn) -> Any:
    return asyncio.run(_with_session(coro_fn))


def _step_summary(step) -> list[str]:
    passed = sum(1 for a in step.assertions if a.passed)
    total = len(step.assertions)
    n_issues = len(step.vision.issues) if step.vision is not None else 0
    return [str(step.idx), step.name, f"{passed}/{total}", str(n_issues), step.progress]


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------


@app.command()
def login() -> None:
    """Interactive Telethon login for the structural eye (MTProto test user)."""
    from graea.client.mtproto import TelethonTransport

    settings = _get_settings()
    settings.ensure_dirs()
    transport = TelethonTransport(settings)

    phone = settings.phone or typer.prompt("Phone number (with country code)")

    def code_callback() -> str:
        return typer.prompt("Login code")

    def password_callback() -> str:
        return typer.prompt("2FA password", hide_input=True)

    async def go() -> str:
        settings.phone = phone
        return await transport.login_interactive(code_callback=code_callback, password_callback=password_callback)

    who = asyncio.run(go())
    _print(f"[green]Logged in as {who}[/]" if _console else f"Logged in as {who}")


@app.command("login-web")
def login_web(headed: bool = typer.Option(False, help="Run a visible browser instead of headless."),
              timeout: int = typer.Option(180, help="Seconds to wait for a QR/login to complete.")) -> None:
    """Log the visual eye (Telegram Web) in; saves a QR screenshot if not already logged in."""
    from graea.visual.web import TelegramWeb

    settings = _get_settings()
    settings.ensure_dirs()
    settings.web_headless = not headed
    web = TelegramWeb(settings)

    async def go() -> None:
        await web.start()
        try:
            if await web.is_logged_in():
                _print("Already logged in.")
                return
            qr_path = str(Path("data") / "login-qr.png")
            Path(qr_path).parent.mkdir(parents=True, exist_ok=True)
            await web.login_qr_screenshot(qr_path)
            _print(f"Scan the QR code saved at {qr_path} with your Telegram app "
                   f"(Settings > Devices > Link Desktop Device). Waiting up to {timeout}s...")
            ok = await web.wait_for_login(timeout_s=timeout)
            _print("Logged in." if ok else "Timed out waiting for login.")
        finally:
            await web.stop()

    asyncio.run(go())


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Print engine health: MTProto connection, web login, vision provider, DB path."""
    async def go(session):
        return await session.health()

    health = _run(go)
    rows = [[k, str(v)] for k, v in health.model_dump(mode="json").items()]
    _table("graea status", ["field", "value"], rows)


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


@app.command()
def run(scenario: str = typer.Argument(..., help="Scenario YAML path, or a bare name under demo/scenarios."),
        bot: Optional[str] = typer.Option(None, help="Override the scenario's target bot."),
        source: Optional[str] = typer.Option(None, "--source", help="Override the scenario's source_path (fingerprinting)."),
        json_out: bool = typer.Option(False, "--json", help="Print the full RunSummary as JSON instead of a table.")) -> None:
    """Run a YAML scenario end-to-end and report per-step pass/fail + vision issues."""
    scen = load_scenario(scenario)
    if bot:
        scen.bot = bot
    if source:
        scen.source_path = source

    async def go(session):
        return await session.run_scenario(scen)

    summary = _run(go)

    if json_out:
        print(summary.model_dump_json(indent=2))
    else:
        rows = [_step_summary(s) for s in summary.step_results]
        _table(f"{summary.scenario} ({summary.run_id}) — {summary.status}",
               ["idx", "name", "assertions", "vision issues", "progress"], rows)
        _print(summary.progress)

    if summary.status == "failed":
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------
# step subcommands (ad hoc)
# --------------------------------------------------------------------------


def _print_step(step) -> None:
    print(step.model_dump_json(indent=2, exclude={
        "replies": {"__all__": {"raw"}},
        "edits": {"__all__": {"raw"}},
        "events": {"__all__": {"message": {"raw"}}},
    }))


@step_app.command("send")
def step_send(text: str) -> None:
    """Send plain text to the bot and print the resulting StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.send_text, text=text))
    _print_step(_run(go))


@step_app.command("command")
def step_command(command: str) -> None:
    """Send a bot command (e.g. "/start") and print the resulting StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.send_command, text=command))
    _print_step(_run(go))


@step_app.command("press")
def step_press(text: Optional[str] = typer.Option(None), index: Optional[int] = typer.Option(None),
               row: Optional[int] = typer.Option(None), col: Optional[int] = typer.Option(None)) -> None:
    """Press an inline button (by text, flat index, or row+col) and print the StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.press_button, button_text=text,
                                          button_index=index, button_row=row, button_col=col))
    _print_step(_run(go))


@step_app.command("file")
def step_file(path: str, caption: Optional[str] = typer.Option(None)) -> None:
    """Send a local file to the bot and print the resulting StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.send_file, file_path=path, caption=caption))
    _print_step(_run(go))


@step_app.command("wait")
def step_wait(timeout_ms: int = typer.Argument(8000)) -> None:
    """Observe without acting for up to timeout_ms and print the resulting StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.wait, timeout_ms=timeout_ms))
    _print_step(_run(go))


@step_app.command("look")
def step_look(last_n: int = typer.Option(5), annotate: bool = typer.Option(False)) -> None:
    """Take a fresh screenshot + vision reading without acting, and print the StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.look, last_n=last_n))
    _print_step(_run(go))


# --------------------------------------------------------------------------
# diff / history / sql / runs
# --------------------------------------------------------------------------


@app.command()
def diff(scenario: Optional[str] = typer.Option(None), a: Optional[str] = typer.Option(None, "--a"),
          b: Optional[str] = typer.Option(None, "--b")) -> None:
    """Diff two runs of a scenario (defaults to the scenario's last two ended runs)."""
    async def go(session):
        return session.diff(scenario=scenario, run_a=a, run_b=b)
    report = _run(go)
    print(report.model_dump_json(indent=2))


@app.command()
def history(scenario: str, assertion: Optional[str] = typer.Option(None)) -> None:
    """Show pass/fail history for an assertion (or all assertions) of a scenario."""
    async def go(session):
        return session.history(scenario, assertion=assertion)
    rows = _run(go)
    if rows:
        _table(f"history: {scenario}", list(rows[0].keys()), [list(r.values()) for r in rows])
    else:
        _print("no history")


@app.command()
def sql(query: str) -> None:
    """Run a read-only SELECT against the Graea DuckDB store."""
    async def go(session):
        return session.sql(query)
    try:
        rows = _run(go)
    except Exception as exc:
        _print(f"error: {exc}")
        raise typer.Exit(code=1)
    if rows:
        _table("sql", list(rows[0].keys()), [list(r.values()) for r in rows])
    else:
        _print("(no rows)")


@app.command()
def runs(scenario: Optional[str] = typer.Option(None)) -> None:
    """List recent runs (optionally filtered to one scenario), most recent first."""
    where = f"WHERE scenario = '{scenario}'" if scenario else ""
    query = f"SELECT run_id, scenario, bot_username, status, started_at, ended_at FROM runs {where} ORDER BY started_at DESC LIMIT 50"

    async def go(session):
        return session.sql(query)
    rows = _run(go)
    if rows:
        _table("runs", list(rows[0].keys()), [list(r.values()) for r in rows])
    else:
        _print("(no runs)")


# --------------------------------------------------------------------------
# serve / mcp
# --------------------------------------------------------------------------


@app.command()
def serve() -> None:
    """Run the HTTP interface (FastAPI + uvicorn)."""
    from graea.interfaces import http as http_iface
    settings = _get_settings()
    settings.ensure_dirs()
    http_iface.serve(settings)


@app.command()
def mcp() -> None:
    """Run the MCP interface over stdio."""
    from graea.interfaces import mcp_server
    mcp_server.main()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
