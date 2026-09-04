"""Tests for the non-interactive / agent-friendly CLI additions:

- `graea status --json` never tracebacks without credentials (exit 2, valid
  JSON, `hint` set).
- `graea doctor` prints the {ok, problems} JSON shape.
- `graea login --code-from-file` picks the code up from a file via the
  code_callback passed to TelethonTransport.login_interactive.
- `TelethonTransport` selects a telethon StringSession when
  `settings.session_string` is set.

No Telegram credentials, no network — TelethonTransport is faked or only
constructed (never connected).
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from graea.config import Settings


def _runner() -> CliRunner:
    return CliRunner()


# --------------------------------------------------------------------------
# status --json without creds
# --------------------------------------------------------------------------


class _UnauthorizedSession:
    """Stand-in TestSession whose start() raises like a real, unauthorized
    TelethonTransport.connect() would — status must catch this, not traceback."""

    def __init__(self, settings):
        self.settings = settings

    async def start(self):
        raise RuntimeError(
            "Graea's Telegram session is not authorized. Run `graea login` and retry."
        )

    async def stop(self):
        pass


class TestStatusNoCreds:
    def test_status_json_no_creds_exit_2_with_hint(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             bot="@testbot")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)
        monkeypatch.setattr(cli, "_session_factory", lambda s: _UnauthorizedSession(s))

        result = _runner().invoke(cli.app, ["status", "--json"])

        assert result.exit_code == 2, result.output
        data = json.loads(result.output)
        assert data["mtproto_connected"] is False
        assert data["hint"]
        assert "not authorized" in data["hint"]
        assert data["db_path"] == str(settings.db)
        assert data["bot"] == "@testbot"

    def test_status_table_no_creds_still_exits_2(self, monkeypatch, tmp_path):
        """Same, without --json: still no traceback, still exit 2."""
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)
        monkeypatch.setattr(cli, "_session_factory", lambda s: _UnauthorizedSession(s))

        result = _runner().invoke(cli.app, ["status"])

        assert result.exit_code == 2, result.output
        assert result.exception is None or isinstance(result.exception, SystemExit)


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------


class TestDoctor:
    def test_doctor_json_shape(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        result = _runner().invoke(cli.app, ["doctor"])

        data = json.loads(result.output)
        assert set(data.keys()) >= {"ok", "problems", "vision_reachable"}
        assert isinstance(data["ok"], bool)
        assert isinstance(data["problems"], list)
        assert all(isinstance(p, str) for p in data["problems"])
        # No api_id/api_hash/bot set on this Settings -> ok must be False and
        # exit code 1, with those specific problems named.
        assert data["ok"] is False
        assert result.exit_code == 1
        joined = " ".join(data["problems"])
        assert "GRAEA_API_ID" in joined
        assert "GRAEA_BOT" in joined

    def test_doctor_writable_data_dir_passes_that_check(self, monkeypatch, tmp_path):
        from graea.interfaces import cli

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             api_id=123, api_hash="abc", bot="@testbot")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        result = _runner().invoke(cli.app, ["doctor"])
        data = json.loads(result.output)
        joined = " ".join(data["problems"])
        assert "not writable" not in joined
        assert "GRAEA_API_ID" not in joined
        assert "GRAEA_BOT" not in joined


# --------------------------------------------------------------------------
# login --code-from-file
# --------------------------------------------------------------------------


class _FakeTransport:
    """Stands in for TelethonTransport; captures the code_callback and calls
    it immediately (as telethon's real login flow would when it needs a code),
    so the test observes exactly what `graea login` handed it."""

    def __init__(self, settings):
        self.settings = settings

    async def login_interactive(self, code_callback=None, password_callback=None):
        assert code_callback is not None
        code = code_callback()
        return f"+15551234567 (code={code})"


class TestLoginCodeFromFile:
    def test_code_from_file_is_read_and_deleted(self, monkeypatch, tmp_path):
        from graea.interfaces import cli
        import graea.client.mtproto as mtproto_mod

        code_path = tmp_path / "login-code.txt"
        code_path.write_text("998877\n")

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             phone="+15551234567")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)
        monkeypatch.setattr(mtproto_mod, "TelethonTransport", _FakeTransport)

        result = _runner().invoke(cli.app, ["login", "--code-from-file", str(code_path)])

        assert result.exit_code == 0, result.output
        assert "GRAEA_LOGIN: ok" in result.output
        assert "998877" in result.output
        assert not code_path.exists()

    def test_login_code_env_var_takes_priority(self, monkeypatch, tmp_path):
        from graea.interfaces import cli
        import graea.client.mtproto as mtproto_mod

        code_path = tmp_path / "login-code.txt"  # deliberately left absent

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             phone="+15551234567")
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)
        monkeypatch.setattr(mtproto_mod, "TelethonTransport", _FakeTransport)
        monkeypatch.setenv("GRAEA_LOGIN_CODE", "112233")

        result = _runner().invoke(cli.app, ["login", "--code-from-file", str(code_path)])

        assert result.exit_code == 0, result.output
        assert "112233" in result.output


# --------------------------------------------------------------------------
# StringSession selection
# --------------------------------------------------------------------------


class TestStringSessionSelection:
    def test_uses_string_session_when_configured(self, tmp_path):
        from telethon.crypto import AuthKey
        from telethon.sessions import StringSession, SQLiteSession
        from graea.client.mtproto import TelethonTransport

        seed = StringSession()
        seed.set_dc(2, "149.154.167.51", 443)
        seed.auth_key = AuthKey(data=b"\x01" * 256)  # dummy key; never actually connects
        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             api_id=123, api_hash="deadbeef",
                             session_string=StringSession.save(seed))

        transport = TelethonTransport(settings)

        assert isinstance(transport.client.session, StringSession)
        assert not isinstance(transport.client.session, SQLiteSession)

    def test_uses_file_session_when_no_string_session(self, tmp_path):
        from telethon.sessions import StringSession
        from graea.client.mtproto import TelethonTransport

        settings = Settings(db=tmp_path / "g.duckdb", shots=tmp_path / "shots",
                             session=tmp_path / "g.session", web_profile=tmp_path / "web",
                             api_id=123, api_hash="deadbeef")

        transport = TelethonTransport(settings)

        assert not isinstance(transport.client.session, StringSession)
