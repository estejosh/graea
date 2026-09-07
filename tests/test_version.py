"""Tests for graea/version.py: semver compare, the on-disk cache, being
disabled by settings/GRAEA_OFFLINE, network-error handling, and per-install-mode
how_to_update — plus `graea version`/`graea update --check` and the MCP
`graea_update_check` tool.

No real network: httpx.get is monkeypatched wherever a fetch might happen.
"""
from __future__ import annotations

import json

import httpx
import pytest

from graea.config import Settings
from graea.models import UpdateInfo
from graea import version as version_mod


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        db=tmp_path / "graea.duckdb",
        shots=tmp_path / "shots",
        session=tmp_path / "graea.session",
        web_profile=tmp_path / "web-profile",
        bot="@testbot",
        **overrides,
    )


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


# --------------------------------------------------------------------------
# semver compare
# --------------------------------------------------------------------------


class TestSemver:
    def test_newer_patch(self):
        assert version_mod.is_newer("0.1.1", "0.1.0") is True

    def test_newer_with_leading_v(self):
        assert version_mod.is_newer("v0.2.0", "0.1.0") is True

    def test_equal_is_not_newer(self):
        assert version_mod.is_newer("0.1.0", "v0.1.0") is False

    def test_older_is_not_newer(self):
        assert version_mod.is_newer("0.1.0", "0.2.0") is False

    def test_garbage_never_raises(self):
        # unparsable version strings fall back to (0, 0, 0) rather than raising
        assert version_mod.is_newer("not-a-version", "0.1.0") is False
        assert version_mod.is_newer("0.1.0", "also-garbage") is True


# --------------------------------------------------------------------------
# disabled paths
# --------------------------------------------------------------------------


class TestDisabled:
    def test_offline_env_disables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GRAEA_OFFLINE", "1")
        settings = _settings(tmp_path)

        def boom(*a, **k):
            raise AssertionError("must not touch the network when GRAEA_OFFLINE=1")
        monkeypatch.setattr(httpx, "get", boom)

        assert version_mod.check_for_update(settings) is None

    def test_settings_check_updates_false_disables(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GRAEA_OFFLINE", raising=False)
        settings = _settings(tmp_path, check_updates=False)

        def boom(*a, **k):
            raise AssertionError("must not touch the network when check_updates=False")
        monkeypatch.setattr(httpx, "get", boom)

        assert version_mod.check_for_update(settings) is None


# --------------------------------------------------------------------------
# network error handling
# --------------------------------------------------------------------------


class TestNetworkError:
    def test_connection_error_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GRAEA_OFFLINE", raising=False)
        settings = _settings(tmp_path)

        def raise_connect_error(*a, **k):
            raise httpx.ConnectError("no network")
        monkeypatch.setattr(httpx, "get", raise_connect_error)

        assert version_mod.check_for_update(settings) is None

    def test_404_falls_back_to_tags_then_gives_up(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GRAEA_OFFLINE", raising=False)
        settings = _settings(tmp_path)

        def get_404(url, timeout=None, headers=None):
            return _FakeResponse(404, {})
        monkeypatch.setattr(httpx, "get", get_404)

        assert version_mod.check_for_update(settings) is None


# --------------------------------------------------------------------------
# happy path + cache hit/miss
# --------------------------------------------------------------------------


class TestCache:
    def test_fetch_and_cache_then_hit(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GRAEA_OFFLINE", raising=False)
        settings = _settings(tmp_path)
        calls = {"n": 0}

        def get_release(url, timeout=None, headers=None):
            calls["n"] += 1
            assert "releases/latest" in url
            return _FakeResponse(200, {"tag_name": "v9.9.9", "html_url": "https://example.com/r"})
        monkeypatch.setattr(httpx, "get", get_release)

        info = version_mod.check_for_update(settings)
        assert info is not None
        assert info.latest == "9.9.9"
        assert info.update_available is True
        assert info.html_url == "https://example.com/r"
        assert calls["n"] == 1

        cache_path = tmp_path / "update-check.json"
        assert cache_path.exists()

        # second call within 24h must be a cache hit: no further network calls
        info2 = version_mod.check_for_update(settings)
        assert info2 is not None
        assert info2.latest == "9.9.9"
        assert calls["n"] == 1

    def test_force_bypasses_cache(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GRAEA_OFFLINE", raising=False)
        settings = _settings(tmp_path)
        calls = {"n": 0}

        def get_release(url, timeout=None, headers=None):
            calls["n"] += 1
            return _FakeResponse(200, {"tag_name": "v9.9.9", "html_url": None})
        monkeypatch.setattr(httpx, "get", get_release)

        version_mod.check_for_update(settings)
        version_mod.check_for_update(settings, force=True)
        assert calls["n"] == 2

    def test_releases_404_falls_back_to_tags(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GRAEA_OFFLINE", raising=False)
        settings = _settings(tmp_path)

        def get(url, timeout=None, headers=None):
            if "releases/latest" in url:
                return _FakeResponse(404, {})
            assert "tags" in url
            return _FakeResponse(200, [{"name": "v0.0.1"}])
        monkeypatch.setattr(httpx, "get", get)

        info = version_mod.check_for_update(settings)
        assert info is not None
        assert info.latest == "0.0.1"

    def test_stale_cache_is_refetched(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GRAEA_OFFLINE", raising=False)
        settings = _settings(tmp_path)
        cache_path = tmp_path / "update-check.json"
        stale = UpdateInfo(
            current=version_mod.__version__, latest="0.0.1", update_available=False,
        )
        # backdate checked_at by writing then patching the JSON's timestamp
        from datetime import timedelta
        stale_dict = json.loads(stale.model_dump_json())
        old_ts = (version_mod.utcnow() - timedelta(hours=48)).isoformat()
        stale_dict["checked_at"] = old_ts
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(stale_dict))

        calls = {"n": 0}

        def get_release(url, timeout=None, headers=None):
            calls["n"] += 1
            return _FakeResponse(200, {"tag_name": "v1.2.3", "html_url": None})
        monkeypatch.setattr(httpx, "get", get_release)

        info = version_mod.check_for_update(settings)
        assert calls["n"] == 1
        assert info.latest == "1.2.3"


# --------------------------------------------------------------------------
# install mode / how_to_update
# --------------------------------------------------------------------------


class TestInstallMode:
    def test_container_env_wins(self, monkeypatch):
        monkeypatch.setenv("GRAEA_IN_CONTAINER", "1")
        assert version_mod.detect_install_mode() == "container"
        cmds = version_mod.how_to_update("container")
        assert any("podman pull" in c for c in cmds)

    def test_editable_when_repo_detected(self, monkeypatch):
        monkeypatch.delenv("GRAEA_IN_CONTAINER", raising=False)
        monkeypatch.setattr(version_mod, "repo_root", lambda: __import__("pathlib").Path("/repo"))
        assert version_mod.detect_install_mode() == "editable"
        cmds = version_mod.how_to_update("editable")
        assert any("git pull" in c for c in cmds)
        assert any("playwright install chromium" in c for c in cmds)

    def test_pip_when_no_repo_no_container(self, monkeypatch):
        monkeypatch.delenv("GRAEA_IN_CONTAINER", raising=False)
        monkeypatch.setattr(version_mod, "repo_root", lambda: None)
        assert version_mod.detect_install_mode() == "pip"
        cmds = version_mod.how_to_update("pip")
        assert any("pip install -U git+" in c for c in cmds)


# --------------------------------------------------------------------------
# CLI: version / update --check
# --------------------------------------------------------------------------


class TestCLIVersionUpdate:
    def test_version_command_prints_version(self):
        from typer.testing import CliRunner
        from graea.interfaces.cli import app

        result = CliRunner().invoke(app, ["version"])
        assert result.exit_code == 0, result.output
        assert version_mod.__version__ in result.output

    def test_update_check_reports_available(self, monkeypatch, tmp_path):
        from typer.testing import CliRunner
        from graea.interfaces import cli

        settings = _settings(tmp_path)
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        info = UpdateInfo(current="0.1.0", latest="9.9.9", update_available=True,
                           how_to_update=["do the thing"])
        monkeypatch.setattr(cli, "check_for_update", lambda s, force=False: info)

        result = CliRunner().invoke(cli.app, ["update", "--check"])
        assert result.exit_code == 0, result.output
        assert "0.1.0 -> 9.9.9" in result.output
        assert "do the thing" in result.output

    def test_update_check_reports_up_to_date(self, monkeypatch, tmp_path):
        from typer.testing import CliRunner
        from graea.interfaces import cli

        settings = _settings(tmp_path)
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)

        info = UpdateInfo(current="0.1.0", latest="0.1.0", update_available=False)
        monkeypatch.setattr(cli, "check_for_update", lambda s, force=False: info)

        result = CliRunner().invoke(cli.app, ["update", "--check"])
        assert result.exit_code == 0, result.output
        assert "up to date" in result.output

    def test_update_check_handles_none(self, monkeypatch, tmp_path):
        from typer.testing import CliRunner
        from graea.interfaces import cli

        settings = _settings(tmp_path)
        monkeypatch.setattr(cli, "_get_settings", lambda: settings)
        monkeypatch.setattr(cli, "check_for_update", lambda s, force=False: None)

        result = CliRunner().invoke(cli.app, ["update", "--check"])
        assert result.exit_code == 0, result.output
        assert "could not check" in result.output


# --------------------------------------------------------------------------
# MCP graea_update_check
# --------------------------------------------------------------------------


class TestMCPUpdateCheck:
    @pytest.mark.asyncio
    async def test_forces_check_and_returns_info(self, patched_interfaces, monkeypatch):
        import graea.interfaces.mcp_server as mcp_server

        info = UpdateInfo(current="0.1.0", latest="9.9.9", update_available=True,
                           how_to_update=["upgrade now"])
        calls = {"force": None}

        def fake_check(settings, force=False):
            calls["force"] = force
            return info
        monkeypatch.setattr(mcp_server, "check_for_update", fake_check)

        tool = mcp_server.mcp._tool_manager.get_tool("graea_update_check")
        content = await tool.run({})
        data = json.loads(content[0].text)

        assert calls["force"] is True
        assert data["latest"] == "9.9.9"
        assert data["update_available"] is True

    @pytest.mark.asyncio
    async def test_returns_note_when_check_disabled(self, patched_interfaces, monkeypatch):
        import graea.interfaces.mcp_server as mcp_server
        monkeypatch.setattr(mcp_server, "check_for_update", lambda settings, force=False: None)

        tool = mcp_server.mcp._tool_manager.get_tool("graea_update_check")
        content = await tool.run({})
        data = json.loads(content[0].text)

        assert data["update_available"] is False
        assert "note" in data

    @pytest.mark.asyncio
    async def test_status_includes_version_and_cached_update(self, patched_interfaces, monkeypatch):
        import graea.interfaces.mcp_server as mcp_server

        cached = UpdateInfo(current="0.1.0", latest="0.2.0", update_available=True)
        # get_session() (re-)runs check_for_update at session start, which
        # would clobber a directly-set _cached_update — patch the source
        # function instead so the normal session-start flow picks it up.
        monkeypatch.setattr(mcp_server, "check_for_update", lambda settings, force=False: cached)

        tool = mcp_server.mcp._tool_manager.get_tool("graea_status")
        content = await tool.run({})
        data = json.loads(content[0].text)

        assert data["version"] == mcp_server.__version__
        assert data["update"]["latest"] == "0.2.0"

    @pytest.mark.asyncio
    async def test_step_notes_carry_update_banner_when_cached(self, patched_interfaces, monkeypatch):
        import graea.interfaces.mcp_server as mcp_server

        cached = UpdateInfo(current="0.1.0", latest="0.5.0", update_available=True)
        monkeypatch.setattr(mcp_server, "check_for_update", lambda settings, force=False: cached)

        tool = mcp_server.mcp._tool_manager.get_tool("graea_send")
        content = await tool.run({"text": "hi"})
        data = json.loads(content[0].text)

        assert any("update available" in n for n in data["notes"])
