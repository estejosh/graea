"""Tests for the first-run field-report fixes (see CHANGELOG 0.1.2):

- doctor validates credential *values*, not just presence, and reports a
  non-secret `config` summary.
- doctor's data-dir-not-writable problem carries the rootless-podman
  remediation text when the write test raises PermissionError.
- `graea login` never prints a Rich/Python traceback for a Telethon auth
  error; it prints one `GRAEA_LOGIN: error ...` line and exits 1.
- Inside the container (GRAEA_IN_CONTAINER=1), the login "waiting for
  code" / QR "scan" lines print both the container path and the
  host-side ./data equivalent.
- `status --json`'s first MTProto connect is capped by
  GRAEA_CONNECT_TIMEOUT_S and reports a clean timeout hint instead of
  hanging.
- every documented `podman run` invocation passes --userns=keep-id
  (rootless podman bind-mount fix).

No Telegram credentials, no network.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from graea.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def _runner() -> CliRunner:
    return CliRunner()


# --------------------------------------------------------------------------
# doctor: credential *value* validation + config object
# --------------------------------------------------------------------------


class TestDoctorCredentialValues:
    def test_placeholder_bot_and_missing_creds(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             bot="@your_bot")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        result = _runner().invoke(cli.app, ["doctor"])
        data = json.loads(result.output)

        assert result.exit_code == 1
        assert data["ok"] is False
        assert "GRAEA_API_ID not set" in data["problems"]
        assert "GRAEA_API_HASH not set" in data["problems"]
        assert "GRAEA_PHONE not set (needed for first login; not needed if " \
               "GRAEA_SESSION_STRING is set)" in data["problems"]
        assert "GRAEA_BOT is still the placeholder @your_bot" in data["problems"]

        assert data["config"] == {
            "api_id": "missing",
            "api_hash": "missing",
            "phone": "missing",
            "session_string": "missing",
            "bot": "placeholder",
        }

    def test_missing_bot_reported_separately_from_placeholder(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        result = _runner().invoke(cli.app, ["doctor"])
        data = json.loads(result.output)

        assert "GRAEA_BOT not set" in data["problems"]
        assert not any("placeholder" in p for p in data["problems"])
        assert data["config"]["bot"] == "missing"

    def test_short_api_hash_flagged_suspicious(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             api_id=123, api_hash="tooshort", bot="@testbot", phone="+15551234567")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        result = _runner().invoke(cli.app, ["doctor"])
        data = json.loads(result.output)

        assert data["config"]["api_hash"] == "suspicious"
        assert any("GRAEA_API_HASH" in p and "looks wrong" in p for p in data["problems"])

    def test_session_string_skips_phone_problem(self, monkeypatch, tmp_path):
        """Missing GRAEA_PHONE is not a problem when GRAEA_SESSION_STRING is set."""
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             api_id=123, api_hash="0123456789abcdef", bot="@testbot",
                             session_string="1abc")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        result = _runner().invoke(cli.app, ["doctor"])
        data = json.loads(result.output)

        assert not any("GRAEA_PHONE" in p for p in data["problems"])
        assert data["config"]["session_string"] == "set"
        assert data["config"]["phone"] == "missing"

    def test_config_never_includes_secret_values(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             api_id=123, api_hash="supersecrethash1234", phone="+15551234567",
                             bot="@testbot")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        result = _runner().invoke(cli.app, ["doctor"])
        assert "supersecrethash1234" not in result.output
        assert "+15551234567" not in result.output


class TestSettingsEmptyEnvValues:
    """A fresh .env (written by install.sh from .env.example) has
    `GRAEA_API_ID=` / `GRAEA_API_HASH=` / `GRAEA_PHONE=` as empty strings,
    not absent — Settings must treat that as unset (None), not crash trying
    to parse "" as an int."""

    def test_empty_env_values_fall_back_to_defaults(self, tmp_path, monkeypatch):
        env_path = tmp_path / ".env"
        env_path.write_text(
            "GRAEA_API_ID=\nGRAEA_API_HASH=\nGRAEA_PHONE=\nGRAEA_BOT=@your_bot\n"
        )
        monkeypatch.chdir(tmp_path)

        settings = Settings()

        assert settings.api_id is None
        assert settings.api_hash is None
        assert settings.phone is None
        assert settings.bot == "@your_bot"


# --------------------------------------------------------------------------
# doctor: EACCES remediation text
# --------------------------------------------------------------------------


class TestDoctorEaccesRemediation:
    def test_permission_error_gets_keepid_remediation(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             api_id=123, api_hash="0123456789abcdef", bot="@testbot",
                             phone="+15551234567")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        def boom(self, *a, **kw):
            raise PermissionError(13, "Permission denied")
        monkeypatch.setattr(Path, "write_text", boom)

        result = _runner().invoke(cli.app, ["doctor"])
        data = json.loads(result.output)

        joined = " ".join(data["problems"])
        assert "data dir not writable" in joined
        assert "Permission denied" in joined
        assert "--userns=keep-id" in joined
        assert "chmod -R a+rwX ./data" in joined
        assert "AGENTS.md" in joined

    def test_permission_error_in_container_mentions_host_path(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             api_id=123, api_hash="0123456789abcdef", bot="@testbot",
                             phone="+15551234567")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)
        monkeypatch.setenv("GRAEA_IN_CONTAINER", "1")

        def boom(self, *a, **kw):
            raise PermissionError(13, "Permission denied")
        monkeypatch.setattr(Path, "write_text", boom)

        result = _runner().invoke(cli.app, ["doctor"])
        data = json.loads(result.output)
        joined = " ".join(data["problems"])
        assert "bind-mounted" in joined
        assert "./data" in joined


# --------------------------------------------------------------------------
# login: no traceback on Telethon auth errors
# --------------------------------------------------------------------------


class _FailingTransport:
    def __init__(self, settings, exc):
        self.settings = settings
        self._exc = exc

    async def login_interactive(self, code_callback=None, password_callback=None):
        raise self._exc


class TestLoginErrorHandling:
    @pytest.mark.parametrize("exc_factory,expected_hint_fragment", [
        (lambda: __import__("telethon.errors", fromlist=["PhoneNumberInvalidError"])
            .PhoneNumberInvalidError(request=None), "GRAEA_PHONE format"),
        (lambda: __import__("telethon.errors", fromlist=["PhoneCodeInvalidError"])
            .PhoneCodeInvalidError(request=None), "code was wrong"),
        (lambda: __import__("telethon.errors", fromlist=["PhoneCodeExpiredError"])
            .PhoneCodeExpiredError(request=None), "code expired"),
        (lambda: __import__("telethon.errors", fromlist=["SessionPasswordNeededError"])
            .SessionPasswordNeededError(request=None), "GRAEA_2FA_PASSWORD"),
        (lambda: __import__("telethon.errors", fromlist=["ApiIdInvalidError"])
            .ApiIdInvalidError(request=None), "my.telegram.org"),
    ])
    def test_telethon_rpc_error_prints_one_line_no_traceback(
        self, monkeypatch, tmp_path, exc_factory, expected_hint_fragment
    ):
        from graea.interfaces import cli
        import graea.client.mtproto as mtproto_mod

        exc = exc_factory()
        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             phone="+15551234567")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)
        monkeypatch.setattr(mtproto_mod, "TelethonTransport",
                             lambda s: _FailingTransport(s, exc))

        result = _runner().invoke(cli.app, ["login"])

        assert result.exit_code == 1
        assert "Traceback" not in result.output
        assert f"GRAEA_LOGIN: error {type(exc).__name__}" in result.output
        assert expected_hint_fragment in result.output

    def test_flood_wait_error_includes_seconds(self, monkeypatch, tmp_path):
        from graea.interfaces import cli
        import graea.client.mtproto as mtproto_mod
        from telethon.errors import FloodWaitError

        exc = FloodWaitError(request=None, capture=42)
        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             phone="+15551234567")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)
        monkeypatch.setattr(mtproto_mod, "TelethonTransport",
                             lambda s: _FailingTransport(s, exc))

        result = _runner().invoke(cli.app, ["login"])

        assert result.exit_code == 1
        assert "Traceback" not in result.output
        assert "GRAEA_LOGIN: error FloodWaitError" in result.output
        assert "42s" in result.output

    def test_unexpected_exception_anywhere_prints_graea_error_not_traceback(self, monkeypatch, tmp_path):
        """Top-level guard: an exception that isn't RPCError/RuntimeError/OSError
        (here in a totally different command) still never tracebacks."""
        from graea.interfaces import cli

        def boom():
            raise ValueError("kaboom")
        monkeypatch.setattr(cli, "version", boom, raising=False)
        # Exercise the guard directly since `version` itself is trivial:
        guarded = cli._guarded(boom)
        with pytest.raises(Exception) as exc_info:
            guarded()
        # typer.Exit is raised, not the original ValueError
        import typer
        assert isinstance(exc_info.value, typer.Exit)


# --------------------------------------------------------------------------
# container dual-path messages
# --------------------------------------------------------------------------


class TestContainerDualPath:
    def test_login_waiting_line_shows_host_path_in_container(self, monkeypatch, tmp_path, capsys):
        from graea.interfaces import cli

        monkeypatch.setenv("GRAEA_IN_CONTAINER", "1")
        cli._print_path_line("GRAEA_LOGIN: waiting for code, write it to ", "/data/login-code.txt")
        out = " ".join(capsys.readouterr().out.split())
        assert "GRAEA_LOGIN: waiting for code, write it to /data/login-code.txt" in out
        assert "(container)" in out
        assert "./data/login-code.txt on the host" in out

    def test_login_waiting_line_single_path_outside_container(self, monkeypatch, capsys):
        from graea.interfaces import cli

        monkeypatch.delenv("GRAEA_IN_CONTAINER", raising=False)
        cli._print_path_line("GRAEA_LOGIN: waiting for code, write it to ", "/data/login-code.txt")
        out = capsys.readouterr().out
        assert "GRAEA_LOGIN: waiting for code, write it to /data/login-code.txt" in out
        assert "(container)" not in out

    def test_login_web_scan_line_dual_path(self, monkeypatch, capsys):
        from graea.interfaces import cli

        monkeypatch.setenv("GRAEA_IN_CONTAINER", "1")
        cli._print_path_line("GRAEA_LOGIN_WEB: scan ", "/data/login-qr.png")
        out = " ".join(capsys.readouterr().out.split())
        assert "GRAEA_LOGIN_WEB: scan /data/login-qr.png" in out
        assert "./data/login-qr.png on the host" in out

    def test_path_outside_data_mount_not_duplicated(self, monkeypatch, capsys):
        from graea.interfaces import cli

        monkeypatch.setenv("GRAEA_IN_CONTAINER", "1")
        cli._print_path_line("GRAEA_LOGIN: waiting for code, write it to ", "/tmp/somewhere/code.txt")
        out = capsys.readouterr().out
        assert "(container)" not in out


# --------------------------------------------------------------------------
# status: MTProto connect timeout
# --------------------------------------------------------------------------


class TestConnectTimeout:
    def test_runner_start_times_out_cleanly(self, tmp_path):
        import asyncio
        from graea.engine.runner import TestSession

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             connect_timeout_s=1)

        class SlowTransport:
            async def connect(self):
                await asyncio.sleep(5)

            async def disconnect(self):
                pass

            async def is_connected(self):
                return False

            async def me(self):
                return None

        session = TestSession(settings, transport=SlowTransport())

        async def go():
            with pytest.raises(TimeoutError) as exc_info:
                await session.start()
            return exc_info.value

        exc = asyncio.run(go())
        assert "timed out after 1s" in str(exc)
        assert "re-run" in str(exc)

    def test_status_json_exits_2_with_timeout_hint(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             connect_timeout_s=1)
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        class SlowSession:
            def __init__(self, settings):
                pass

            async def start(self):
                raise TimeoutError(
                    f"Telegram connect timed out after {settings.connect_timeout_s}s "
                    "(first connect can be slow; re-run)"
                )

            async def stop(self):
                pass

        monkeypatch.setattr(cli, "_session_factory", lambda s: SlowSession(s))

        result = _runner().invoke(cli.app, ["status", "--json"])

        assert result.exit_code == 2
        data = json.loads(result.output)
        assert data["mtproto_connected"] is False
        assert "timed out after 1s" in data["hint"]
        assert "re-run" in data["hint"]


# --------------------------------------------------------------------------
# every documented `podman run` passes --userns=keep-id
# --------------------------------------------------------------------------


_RUNNABLE_PODMAN_LINE_RE = re.compile(r"podman run\b.*(-v |--env-file|-e )")


class TestPodmanKeepId:
    @pytest.mark.parametrize("relpath", [
        "AGENTS.md",
        "README.md",
        "docs/SETUP.md",
        "install.sh",
    ])
    def test_no_podman_run_invocation_missing_keep_id(self, relpath):
        path = REPO_ROOT / relpath
        if not path.exists():
            pytest.skip(f"{relpath} does not exist")
        text = path.read_text()
        offenders = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _RUNNABLE_PODMAN_LINE_RE.search(line) and "--userns=keep-id" not in line:
                offenders.append((lineno, line.strip()))
        assert offenders == [], f"{relpath}: podman run line(s) missing --userns=keep-id: {offenders}"

    def test_agent_json_command_arrays_have_keep_id(self):
        data = json.loads((REPO_ROOT / "graea.agent.json").read_text())

        offenders = []

        def walk(node):
            if isinstance(node, list):
                if len(node) >= 2 and node[0] == "podman" and node[1] == "run":
                    if "--userns=keep-id" not in node:
                        offenders.append(node)
                for item in node:
                    walk(item)
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)

        walk(data)
        assert offenders == [], f"podman run arrays missing --userns=keep-id: {offenders}"
