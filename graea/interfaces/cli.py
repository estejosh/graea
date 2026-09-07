"""`graea` typer CLI — login, run scenarios, ad hoc steps, history/diff/sql, and
launching the HTTP/MCP interfaces.

Every command builds its own `Settings`, constructs a session for the
duration of the command (`asyncio.run`), and tears it down again — no
long-lived process state between invocations (use `graea serve` or
`graea mcp` for a persistent session).
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

import click
import typer
from telethon.errors import RPCError

from graea.config import Settings, get_settings
from graea.engine.runner import TestSession
from graea.engine.scenario import load_scenario
from graea.models import Action, ActionKind, Health
from graea.version import __version__, check_for_update, detect_install_mode, how_to_update

try:
    from rich.console import Console
    from rich.table import Table
    _console: Optional["Console"] = Console()
except ImportError:  # pragma: no cover - exercised only when rich is absent
    _console = None

# pretty_exceptions_enable=False: never let Rich (or Click's default) print a
# full traceback for an uncaught exception — every command is wrapped by
# `_guarded` below instead, which prints one clean line.
app = typer.Typer(name="graea", help="Give an LLM eyes on a Telegram bot.", pretty_exceptions_enable=False)
step_app = typer.Typer(help="Ad hoc single steps against the current/adhoc run.")
app.add_typer(step_app, name="step")
session_app = typer.Typer(help="Session file <-> StringSession helpers.")
app.add_typer(session_app, name="session")

# Swapped out by tests. Production default: the real TestSession.
_session_factory: Callable[[Settings], Any] = TestSession
_get_settings: Callable[[], Settings] = get_settings


# --------------------------------------------------------------------------
# error handling: no Rich/Python tracebacks ever reach stdout/stderr
# --------------------------------------------------------------------------

# telethon.errors.RPCError subclass -> short human hint (never echoes
# Telethon internals beyond the exception's own class name).
_LOGIN_ERROR_HINTS: dict[str, str] = {
    "PhoneNumberInvalidError": "check GRAEA_PHONE format (+<countrycode><number>, digits only)",
    "PhoneCodeInvalidError": "the code was wrong; re-run login",
    "PhoneCodeExpiredError": "code expired; re-run login",
    "SessionPasswordNeededError": "2FA enabled; set GRAEA_2FA_PASSWORD",
    "ApiIdInvalidError": "GRAEA_API_ID/GRAEA_API_HASH rejected; check my.telegram.org",
}


def _login_error_hint(exc: BaseException) -> str:
    """Maps a login-flow exception to a short, actionable hint string. Never
    surfaces Telethon internals (tracebacks, request objects) — just the
    exception's class name plus this hint."""
    name = type(exc).__name__
    if name in _LOGIN_ERROR_HINTS:
        return _LOGIN_ERROR_HINTS[name]
    if name == "FloodWaitError":
        seconds = getattr(exc, "seconds", "?")
        return f"Telegram rate limit; wait {seconds}s"
    text = str(exc).strip()
    return text.splitlines()[0] if text else name


def _print_login_error(exc: BaseException) -> None:
    _print_err(f"GRAEA_LOGIN: error {type(exc).__name__} — {_login_error_hint(exc)}")


def _print_err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _guarded(fn: Callable) -> Callable:
    """Wraps a command so an unexpected exception never prints a traceback.

    typer.Exit / click exceptions (BadParameter, UsageError, the plain
    click.exceptions.Exit typer.Exit is built on) pass through untouched so
    their normal exit codes/messages still apply. A telethon RPCError,
    RuntimeError, or OSError bubbling out of a login-ish command has almost
    certainly already been reported via `_print_login_error` at the raise
    site (login/login-web/session export) and re-raised as typer.Exit(1);
    anything else unexpected is reported here as a single
    `GRAEA_ERROR: <Class>: <msg>` line and exits 1."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, click.exceptions.ClickException, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 - intentional last-resort guard
            _print_err(f"GRAEA_ERROR: {type(exc).__name__}: {exc}")
            raise typer.Exit(code=1) from None

    return wrapper


@app.callback()
def _main(ctx: typer.Context) -> None:
    """Runs before every subcommand: prints a one-line stderr update banner if
    a newer graea release exists. Cheap (24h on-disk cache) and never raises.
    Skipped for `mcp`/`serve` so those protocols' stdout/stderr stay clean."""
    if ctx.invoked_subcommand in ("mcp", "serve"):
        return
    try:
        settings = _get_settings()
        info = check_for_update(settings)
        if info is not None and info.update_available:
            print(f"GRAEA_UPDATE: {info.current} -> {info.latest} (run: graea update)", file=sys.stderr)
    except Exception:
        pass


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


def _in_container() -> bool:
    return os.environ.get("GRAEA_IN_CONTAINER") == "1"


def _container_host_path(path: str) -> Optional[str]:
    """If `path` lives under the container's /data mount, returns the
    host-side equivalent under ./data (as documented in AGENTS.md's
    `-v ./data:/data:Z` bind mount) — otherwise None."""
    if path == "/data" or path.startswith("/data/"):
        return "./data" + path[len("/data"):]
    return None


def _print_path_line(prefix: str, path: str) -> None:
    """Prints `<prefix><path>`, or — inside the container, when `path` is
    under the bind-mounted /data — both the container path and its host
    equivalent, so an agent watching from the host knows which file to
    write to (the first path after the prefix is always the stable one to
    parse)."""
    if _in_container():
        host_path = _container_host_path(path)
        if host_path is not None:
            _print(f"{prefix}{path} (container) = {host_path} on the host")
            return
    _print(f"{prefix}{path}")


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------


@app.command()
@_guarded
def login(code_from_file: Optional[str] = typer.Option(
              None, "--code-from-file",
              help="Poll this file for the login code instead of prompting "
                   "(default: <session dir>/login-code.txt when stdin isn't a TTY)."),
          timeout: int = typer.Option(300, help="Seconds to wait for the code via --code-from-file.")) -> None:
    """Telethon login for the structural eye (MTProto test user).

    Non-interactive-safe: never calls input() unless stdin is a TTY and
    --code-from-file was not given. Code source, in priority order:
    the GRAEA_LOGIN_CODE env var, then the --code-from-file path (polled
    every 2s up to --timeout, file deleted once read), then an interactive
    prompt. 2FA password: GRAEA_2FA_PASSWORD env var, else an interactive
    prompt (TTY only).

    Prints `GRAEA_LOGIN: waiting for code, write it to <path>` while
    waiting on the file, and `GRAEA_LOGIN: ok <user>` on success — an agent
    driving this in the background should watch stdout for those lines.
    """
    from graea.client.mtproto import TelethonTransport

    settings = _get_settings()
    settings.ensure_dirs()
    transport = TelethonTransport(settings)

    interactive_ok = sys.stdin.isatty()
    phone = settings.phone
    if not phone:
        if interactive_ok:
            phone = typer.prompt("Phone number (with country code)")
        else:
            raise typer.BadParameter("GRAEA_PHONE is not set and stdin is not a TTY; "
                                      "set GRAEA_PHONE and retry.")

    code_path = Path(code_from_file) if code_from_file else settings.session.parent / "login-code.txt"

    def code_callback() -> str:
        env_code = os.environ.get("GRAEA_LOGIN_CODE")
        if env_code:
            return env_code
        if interactive_ok and code_from_file is None:
            return typer.prompt("Login code")
        _print_path_line("GRAEA_LOGIN: waiting for code, write it to ", str(code_path))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if code_path.exists():
                code = code_path.read_text().strip()
                try:
                    code_path.unlink()
                except OSError:
                    pass
                if code:
                    return code
            time.sleep(2)
        raise RuntimeError(f"GRAEA_LOGIN: timed out waiting for code at {code_path}")

    def password_callback() -> str:
        env_pw = os.environ.get("GRAEA_2FA_PASSWORD")
        if env_pw:
            return env_pw
        if interactive_ok:
            return typer.prompt("2FA password", hide_input=True)
        raise RuntimeError("GRAEA_LOGIN: 2FA password required; set GRAEA_2FA_PASSWORD")

    async def go() -> str:
        settings.phone = phone
        return await transport.login_interactive(code_callback=code_callback, password_callback=password_callback)

    try:
        who = asyncio.run(go())
    except (RPCError, RuntimeError, OSError) as exc:
        _print_login_error(exc)
        raise typer.Exit(code=1) from None
    _print(f"GRAEA_LOGIN: ok {who}")


@app.command("login-web")
@_guarded
def login_web(headed: bool = typer.Option(False, help="Run a visible browser instead of headless."),
              timeout: int = typer.Option(180, help="Seconds to wait for the QR login to complete.")) -> None:
    """Log the visual eye (Telegram Web) in; headless- and non-interactive-safe.

    Writes a QR screenshot to `<shots dir>/../login-qr.png` (e.g.
    /data/login-qr.png in the container) and prints
    `GRAEA_LOGIN_WEB: scan <path>`. WebK QR codes rotate roughly every 30s,
    so the QR is re-screenshotted (same path, overwritten) every 20s while
    polling, each time reprinting the `scan` line, until login completes or
    --timeout elapses. Prints `GRAEA_LOGIN_WEB: ok` on success, or
    `GRAEA_LOGIN_WEB: already` (exit 0) if already logged in.
    """
    from graea.visual.web import TelegramWeb

    settings = _get_settings()
    settings.ensure_dirs()
    settings.web_headless = not headed
    web = TelegramWeb(settings)

    rescreenshot_every_s = 20

    async def go() -> bool:
        await web.start()
        try:
            if await web.is_logged_in():
                _print("GRAEA_LOGIN_WEB: already")
                return True

            qr_path = str(settings.shots.parent / "login-qr.png")
            Path(qr_path).parent.mkdir(parents=True, exist_ok=True)
            await web.login_qr_screenshot(qr_path)
            _print_path_line("GRAEA_LOGIN_WEB: scan ", qr_path)

            deadline = time.monotonic() + timeout
            next_shot = time.monotonic() + rescreenshot_every_s
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                wait_chunk = max(1, int(min(rescreenshot_every_s, remaining)))
                if await web.wait_for_login(timeout_s=wait_chunk):
                    _print("GRAEA_LOGIN_WEB: ok")
                    return True
                if time.monotonic() >= next_shot:
                    await web.login_qr_screenshot(qr_path)
                    _print_path_line("GRAEA_LOGIN_WEB: scan ", qr_path)
                    next_shot = time.monotonic() + rescreenshot_every_s
            _print("GRAEA_LOGIN_WEB: timed out")
            return False
        finally:
            await web.stop()

    try:
        ok = asyncio.run(go())
    except (RPCError, RuntimeError, OSError) as exc:
        _print_login_error(exc)
        raise typer.Exit(code=1) from None
    if not ok:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------
# session export (StringSession)
# --------------------------------------------------------------------------


@session_app.command("export")
@_guarded
def session_export() -> None:
    """Print the StringSession for the current file session, for GRAEA_SESSION_STRING.

    Lets a human log in once (`graea login`) anywhere, then the agent injects
    the printed string as GRAEA_SESSION_STRING into any container/environment
    with zero further human steps (StringSession takes priority over the
    session file — see TelethonTransport).
    """
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    settings = _get_settings()
    settings.ensure_dirs()

    if not settings.api_id or not settings.api_hash:
        _print("error: GRAEA_API_ID / GRAEA_API_HASH must be set to export the session.")
        raise typer.Exit(code=1)
    if not settings.session.exists():
        _print(f"error: no session file at {settings.session}; run `graea login` first.")
        raise typer.Exit(code=1)

    async def go() -> str:
        client = TelegramClient(str(settings.session), settings.api_id, settings.api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise RuntimeError("session file is not authorized; run `graea login` first.")
            return StringSession.save(client.session)
        finally:
            await client.disconnect()

    try:
        result = asyncio.run(go())
    except (RPCError, RuntimeError, OSError) as exc:
        _print_login_error(exc)
        raise typer.Exit(code=1) from None
    print(result)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


@app.command()
@_guarded
def status(json_out: bool = typer.Option(False, "--json", help="Print the Health model as JSON.")) -> None:
    """Print engine health: MTProto connection, web login, vision provider, DB path.

    Never tracebacks even with no Telegram credentials configured: a failed
    connect (e.g. unauthorized/missing session) is caught and reported as
    Health(mtproto_connected=False, hint=<error text>) instead of raising.
    Exit code 0 if mtproto_connected, else 2.
    """
    settings = _get_settings()
    settings.ensure_dirs()
    session = _session_factory(settings)

    async def go() -> Health:
        try:
            health = await session.start()
        except Exception as exc:
            health = Health(
                mtproto_connected=False,
                hint=str(exc),
                db_path=str(settings.db),
                bot=settings.bot_username(),
            )
        finally:
            try:
                await session.stop()
            except Exception:
                pass
        return health

    health = asyncio.run(go())
    health.version = __version__
    try:
        health.update = check_for_update(settings)
    except Exception:
        health.update = None

    if json_out:
        print(health.model_dump_json(indent=2))
    else:
        rows = [[k, str(v)] for k, v in health.model_dump(mode="json").items()]
        _table("graea status", ["field", "value"], rows)

    if not health.mtproto_connected:
        raise typer.Exit(code=2)


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


_BOT_PLACEHOLDERS = {"@your_bot", "your_bot", "changeme", "xxx"}
_PLACEHOLDER_ANGLE_RE = re.compile(r"^@?<.*>$")


def _is_placeholder_bot(bot: str) -> bool:
    """True for values .env.example ships (or an agent left untouched):
    `@your_bot`, `your_bot`, `changeme`, `xxx`, or an angle-bracket
    placeholder like `<your bot username>`."""
    normalized = bot.strip().lower()
    if normalized in _BOT_PLACEHOLDERS:
        return True
    return bool(_PLACEHOLDER_ANGLE_RE.match(normalized))


def _doctor_config_summary(settings: Settings) -> dict[str, str]:
    """Non-secret {field: status} summary for doctor's JSON `config` object.
    Never includes the actual api_hash/phone values, only set/missing/etc."""
    if not settings.bot:
        bot_status = "missing"
    elif _is_placeholder_bot(settings.bot):
        bot_status = "placeholder"
    else:
        bot_status = settings.bot

    if not settings.api_hash:
        api_hash_status = "missing"
    elif len(settings.api_hash) < 16:
        api_hash_status = "suspicious"
    else:
        api_hash_status = "set"

    return {
        "api_id": "set" if settings.api_id else "missing",
        "api_hash": api_hash_status,
        "phone": "set" if settings.phone else "missing",
        "session_string": "set" if settings.session_string else "missing",
        "bot": bot_status,
    }


@app.command()
@_guarded
def doctor() -> None:
    """Offline environment checks — no Telegram connection, no network required.

    Checks: Python version, tesseract on PATH, playwright chromium present
    under PLAYWRIGHT_BROWSERS_PATH (or the default cache dir), required env
    vars set (api_id/api_hash/phone/bot — values, not just presence: a
    too-short api_hash or a placeholder bot username is flagged), data dir
    writable. Also best-effort probes the vision endpoint (GET
    base_url/models, 3s timeout) but never fails the check on that alone.
    Prints JSON {ok, problems, vision_reachable, config}. `config` never
    echoes the actual api_hash/phone, only set/missing/suspicious. Exit code
    0 if ok, else 1.
    """
    import shutil as _shutil

    settings = _get_settings()
    problems: list[str] = []

    if sys.version_info < (3, 11):
        problems.append(f"python {sys.version.split()[0]} is older than the required 3.11")

    if _shutil.which("tesseract") is None:
        problems.append("tesseract binary not found on PATH")

    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    search_roots = [Path(browsers_path)] if browsers_path else []
    search_roots.append(Path.home() / ".cache" / "ms-playwright")
    chromium_found = any(root.exists() and any(root.glob("chromium*")) for root in search_roots)
    if not chromium_found:
        problems.append("playwright chromium not found (run `playwright install chromium`, "
                         "or set PLAYWRIGHT_BROWSERS_PATH to where it's provisioned)")

    config = _doctor_config_summary(settings)

    if config["api_id"] == "missing":
        problems.append("GRAEA_API_ID not set")
    if config["api_hash"] == "missing":
        problems.append("GRAEA_API_HASH not set")
    elif config["api_hash"] == "suspicious":
        problems.append("GRAEA_API_HASH looks wrong (shorter than 16 chars)")
    if config["phone"] == "missing" and not settings.session_string:
        problems.append("GRAEA_PHONE not set (needed for first login; not needed if "
                         "GRAEA_SESSION_STRING is set)")
    if config["bot"] == "placeholder":
        problems.append(f"GRAEA_BOT is still the placeholder {settings.bot}")
    elif config["bot"] == "missing":
        problems.append("GRAEA_BOT not set")

    try:
        settings.ensure_dirs()
        probe_path = settings.db.parent / ".graea-doctor-write-test"
        probe_path.write_text("ok")
        probe_path.unlink()
    except PermissionError:
        msg = (f"data dir not writable ({settings.db.parent}): Permission denied. "
               "Rootless podman? run the container with --userns=keep-id (see AGENTS.md), "
               "or on the host: chmod -R a+rwX ./data")
        if _in_container():
            msg += (" (this container path is the bind-mounted ./data directory "
                     "on the host)")
        problems.append(msg)
    except Exception as exc:
        problems.append(f"data dir not writable ({settings.db.parent}): {exc}")

    vision_reachable: Optional[bool] = None
    if settings.vision_provider == "openai_compatible":
        import httpx

        url = settings.vision_base_url.rstrip("/") + "/models"
        try:
            resp = httpx.get(url, timeout=3.0, headers={"Authorization": f"Bearer {settings.vision_api_key}"})
            vision_reachable = resp.status_code < 500
        except Exception:
            vision_reachable = False

    result = {
        "ok": len(problems) == 0,
        "problems": problems,
        "vision_reachable": vision_reachable,
        "config": config,
    }
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise typer.Exit(code=1)


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


@app.command()
@_guarded
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
@_guarded
def step_send(text: str) -> None:
    """Send plain text to the bot and print the resulting StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.send_text, text=text))
    _print_step(_run(go))


@step_app.command("command")
@_guarded
def step_command(command: str) -> None:
    """Send a bot command (e.g. "/start") and print the resulting StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.send_command, text=command))
    _print_step(_run(go))


@step_app.command("press")
@_guarded
def step_press(text: Optional[str] = typer.Option(None), index: Optional[int] = typer.Option(None),
               row: Optional[int] = typer.Option(None), col: Optional[int] = typer.Option(None)) -> None:
    """Press an inline button (by text, flat index, or row+col) and print the StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.press_button, button_text=text,
                                          button_index=index, button_row=row, button_col=col))
    _print_step(_run(go))


@step_app.command("file")
@_guarded
def step_file(path: str, caption: Optional[str] = typer.Option(None)) -> None:
    """Send a local file to the bot and print the resulting StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.send_file, file_path=path, caption=caption))
    _print_step(_run(go))


@step_app.command("wait")
@_guarded
def step_wait(timeout_ms: int = typer.Argument(8000)) -> None:
    """Observe without acting for up to timeout_ms and print the resulting StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.wait, timeout_ms=timeout_ms))
    _print_step(_run(go))


@step_app.command("look")
@_guarded
def step_look(last_n: int = typer.Option(5), annotate: bool = typer.Option(False)) -> None:
    """Take a fresh screenshot + vision reading without acting, and print the StepResult."""
    async def go(session):
        return await session.step(Action(kind=ActionKind.look, last_n=last_n))
    _print_step(_run(go))


# --------------------------------------------------------------------------
# diff / history / sql / runs
# --------------------------------------------------------------------------


@app.command()
@_guarded
def diff(scenario: Optional[str] = typer.Option(None), a: Optional[str] = typer.Option(None, "--a"),
          b: Optional[str] = typer.Option(None, "--b")) -> None:
    """Diff two runs of a scenario (defaults to the scenario's last two ended runs)."""
    async def go(session):
        return session.diff(scenario=scenario, run_a=a, run_b=b)
    report = _run(go)
    print(report.model_dump_json(indent=2))


@app.command()
@_guarded
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
@_guarded
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
@_guarded
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
# version / update
# --------------------------------------------------------------------------


@app.command()
@_guarded
def version() -> None:
    """Print the installed graea version."""
    _print(__version__)


@app.command()
@_guarded
def update(check: bool = typer.Option(False, "--check", help="Only check for an update; don't apply it.")) -> None:
    """Check for a newer graea release and, unless --check, apply it.

    Forces a fresh check (bypasses the 24h cache). How the update is applied
    depends on the detected install mode: `podman pull` for a container
    install, `git pull` + `pip install -e .` + `playwright install chromium`
    for an editable/clone install, `pip install -U git+...` otherwise.
    """
    import subprocess

    settings = _get_settings()
    settings.ensure_dirs()
    info = check_for_update(settings, force=True)

    if info is None:
        _print("GRAEA_UPDATE: could not check for updates (offline, disabled, or the check failed).")
        return

    if not info.update_available:
        _print(f"GRAEA_UPDATE: up to date ({info.current}).")
        return

    _print(f"GRAEA_UPDATE: {info.current} -> {info.latest} available.")
    for line in info.how_to_update:
        _print(f"  {line}")

    if check:
        return

    mode = detect_install_mode()

    if mode == "container":
        _print("GRAEA_UPDATE: running podman pull ghcr.io/estejosh/graea:latest")
        result = subprocess.run(["podman", "pull", "ghcr.io/estejosh/graea:latest"])
        if result.returncode != 0:
            _print("GRAEA_UPDATE: pull failed (private repo, no network, or no image published yet).")
            _print("GRAEA_UPDATE: rebuild locally instead:")
            for line in how_to_update("container")[1:]:
                _print(f"  {line}")
            raise typer.Exit(code=1)
    elif mode == "editable":
        from graea.version import repo_root
        root = repo_root() or Path.cwd()
        steps = [
            ["git", "-C", str(root), "pull", "--ff-only"],
            [sys.executable, "-m", "pip", "install", "-e", str(root)],
            [sys.executable, "-m", "playwright", "install", "chromium"],
        ]
        for step in steps:
            _print(f"GRAEA_UPDATE: running {' '.join(step)}")
            result = subprocess.run(step)
            if result.returncode != 0:
                raise typer.Exit(code=1)
    else:  # pip
        step = [sys.executable, "-m", "pip", "install", "-U", "git+https://github.com/estejosh/graea"]
        _print(f"GRAEA_UPDATE: running {' '.join(step)}")
        result = subprocess.run(step)
        if result.returncode != 0:
            raise typer.Exit(code=1)

    _print("GRAEA_UPDATE: done.")


# --------------------------------------------------------------------------
# serve / mcp
# --------------------------------------------------------------------------


@app.command()
@_guarded
def serve(host: Optional[str] = typer.Option(None, "--host", help="Override GRAEA_HTTP_HOST."),
          port: Optional[int] = typer.Option(None, "--port", help="Override GRAEA_HTTP_PORT.")) -> None:
    """Run the HTTP interface (FastAPI + uvicorn)."""
    from graea.interfaces import http as http_iface
    settings = _get_settings()
    settings.ensure_dirs()
    if host:
        settings.http_host = host
    if port:
        settings.http_port = port
    http_iface.serve(settings)


@app.command()
@_guarded
def mcp() -> None:
    """Run the MCP interface over stdio."""
    from graea.interfaces import mcp_server
    mcp_server.main()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
