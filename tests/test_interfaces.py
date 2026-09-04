"""Tests for graea/interfaces/{mcp_server,cli,http}.py against FakeSession.

No Telegram credentials, no network: every interface's session factory is
monkeypatched (see tests/conftest.py::patched_interfaces) to return a
FakeSession with canned models and a real PNG screenshot written via Pillow.
"""
from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import Image as MCPImage
from mcp.types import TextContent


# --------------------------------------------------------------------------
# MCP server
# --------------------------------------------------------------------------


class TestMCPServer:
    async def _call(self, name: str, **kwargs):
        import graea.interfaces.mcp_server as mcp_server
        tool = mcp_server.mcp._tool_manager.get_tool(name)
        assert tool is not None, f"tool {name} not registered"
        return await tool.run(kwargs)

    def test_tools_registered(self, patched_interfaces):
        import graea.interfaces.mcp_server as mcp_server
        names = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
        expected = {
            "graea_start_run", "graea_send", "graea_command", "graea_press_button",
            "graea_send_file", "graea_wait", "graea_look", "graea_assert",
            "graea_run_scenario", "graea_diff", "graea_history", "graea_sql",
            "graea_end_run", "graea_status",
        }
        assert expected <= names

    @pytest.mark.asyncio
    async def test_start_run_returns_text_only(self, patched_interfaces):
        content = await self._call("graea_start_run", scenario="demo")
        assert len(content) == 1
        assert isinstance(content[0], TextContent)
        data = json.loads(content[0].text)
        assert data["scenario"] == "demo"
        assert data["run_id"]

    @pytest.mark.asyncio
    async def test_send_returns_text_and_image(self, patched_interfaces):
        await self._call("graea_start_run", scenario="demo")
        content = await self._call("graea_send", text="hello")
        assert isinstance(content[0], TextContent)
        data = json.loads(content[0].text)
        assert data["name"] == "hello" or "send_text" in data["action"]["kind"]
        assert len(content) == 2
        assert isinstance(content[1], MCPImage)
        # raw and entities are stripped from the compact JSON
        assert "raw" not in content[0].text

    @pytest.mark.asyncio
    async def test_send_image_content_converts(self, patched_interfaces):
        content = await self._call("graea_send", text="hi")
        img = content[1]
        image_content = img.to_image_content()
        assert image_content.type == "image"
        assert image_content.mimeType == "image/png"
        assert len(image_content.data) > 0

    @pytest.mark.asyncio
    async def test_press_button_with_expect_fail(self, patched_interfaces):
        content = await self._call(
            "graea_press_button", text="Yes",
            expect=[{"kind": "text_contains", "value": "FAIL"}],
        )
        data = json.loads(content[0].text)
        assert data["assertions"][0]["passed"] is False

    @pytest.mark.asyncio
    async def test_assert_tool(self, patched_interfaces):
        await self._call("graea_send", text="hi")
        content = await self._call("graea_assert", assertions=[{"kind": "text_contains", "value": "hello"}])
        results = json.loads(content[0].text)
        assert results[0]["passed"] is True

    @pytest.mark.asyncio
    async def test_run_scenario_caps_failing_images(self, patched_interfaces, monkeypatch):
        import graea.interfaces.mcp_server as mcp_server
        from graea.models import Scenario, ScenarioStep, Action, ActionKind
        fake_scenario = Scenario(name="manyfail", steps=[ScenarioStep(name="s1", action=Action(kind=ActionKind.send_text, text="hi"))])
        monkeypatch.setattr(mcp_server, "load_scenario", lambda p: fake_scenario)

        content = await self._call("graea_run_scenario", path_or_name="manyfail")
        images = [c for c in content if isinstance(c, MCPImage)]
        assert len(images) == 6  # capped even though 8 steps fail

    @pytest.mark.asyncio
    async def test_run_scenario_only_failing_get_images(self, patched_interfaces, monkeypatch):
        import graea.interfaces.mcp_server as mcp_server
        from graea.models import Scenario, ScenarioStep, Action, ActionKind
        fake_scenario = Scenario(name="partialfail", steps=[ScenarioStep(name="s1", action=Action(kind=ActionKind.send_text, text="hi"))])
        monkeypatch.setattr(mcp_server, "load_scenario", lambda p: fake_scenario)

        content = await self._call("graea_run_scenario", path_or_name="partialfail")
        data = json.loads(content[0].text)
        n_failing = sum(1 for s in data["step_results"] if not all(a["passed"] for a in s["assertions"]))
        images = [c for c in content if isinstance(c, MCPImage)]
        assert len(images) == n_failing
        assert n_failing < len(data["step_results"])  # some passed

    @pytest.mark.asyncio
    async def test_diff(self, patched_interfaces):
        content = await self._call("graea_diff", scenario="demo")
        data = json.loads(content[0].text)
        assert data["scenario"] == "demo"

    @pytest.mark.asyncio
    async def test_history(self, patched_interfaces):
        content = await self._call("graea_history", scenario="demo")
        rows = json.loads(content[0].text)
        assert rows[0]["run_id"] == "r0001"

    @pytest.mark.asyncio
    async def test_sql_ok(self, patched_interfaces):
        content = await self._call("graea_sql", query="SELECT * FROM runs")
        rows = json.loads(content[0].text)
        assert rows[0]["run_id"] == "r0001"

    @pytest.mark.asyncio
    async def test_sql_rejects_non_select_gracefully(self, patched_interfaces):
        content = await self._call("graea_sql", query="DROP TABLE runs")
        data = json.loads(content[0].text)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_status(self, patched_interfaces):
        content = await self._call("graea_status")
        data = json.loads(content[0].text)
        assert data["mtproto_connected"] is True

    @pytest.mark.asyncio
    async def test_end_run(self, patched_interfaces):
        content = await self._call("graea_end_run", status="passed")
        data = json.loads(content[0].text)
        assert data["status"] == "passed"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


class TestCLI:
    def _runner(self):
        from typer.testing import CliRunner
        return CliRunner()

    def test_status_table(self, patched_interfaces):
        from graea.interfaces.cli import app
        result = self._runner().invoke(app, ["status"])
        assert result.exit_code == 0, result.output
        assert "mtproto_connected" in result.output

    def test_run_scenario_by_name(self, patched_interfaces, tmp_path, monkeypatch):
        from graea.interfaces import cli
        # patch load_scenario to avoid needing a real demo scenario file
        from graea.models import Scenario, ScenarioStep, Action, ActionKind
        fake_scenario = Scenario(name="demo", steps=[ScenarioStep(name="s1", action=Action(kind=ActionKind.send_text, text="hi"))])
        monkeypatch.setattr(cli, "load_scenario", lambda p: fake_scenario)

        result = self._runner().invoke(cli.app, ["run", "demo"])
        assert result.exit_code == 0, result.output
        assert "demo" in result.output

    def test_run_scenario_failure_exit_code(self, patched_interfaces, monkeypatch):
        from graea.interfaces import cli
        from graea.models import Scenario, ScenarioStep, Action, ActionKind
        fake_scenario = Scenario(name="manyfail", steps=[ScenarioStep(name="s1", action=Action(kind=ActionKind.send_text, text="hi"))])
        monkeypatch.setattr(cli, "load_scenario", lambda p: fake_scenario)

        result = self._runner().invoke(cli.app, ["run", "manyfail"])
        assert result.exit_code == 1

    def test_run_json_output(self, patched_interfaces, monkeypatch):
        from graea.interfaces import cli
        from graea.models import Scenario, ScenarioStep, Action, ActionKind
        fake_scenario = Scenario(name="demo", steps=[ScenarioStep(name="s1", action=Action(kind=ActionKind.send_text, text="hi"))])
        monkeypatch.setattr(cli, "load_scenario", lambda p: fake_scenario)

        result = self._runner().invoke(cli.app, ["run", "demo", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["scenario"] == "demo"

    def test_diff_command(self, patched_interfaces):
        from graea.interfaces.cli import app
        result = self._runner().invoke(app, ["diff", "--scenario", "demo"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["scenario"] == "demo"

    def test_sql_command(self, patched_interfaces):
        from graea.interfaces.cli import app
        result = self._runner().invoke(app, ["sql", "SELECT * FROM runs"])
        assert result.exit_code == 0, result.output
        assert "run_id" in result.output

    def test_sql_command_error(self, patched_interfaces):
        from graea.interfaces.cli import app
        result = self._runner().invoke(app, ["sql", "DELETE FROM runs"])
        assert result.exit_code == 1
        assert "error" in result.output

    def test_step_send(self, patched_interfaces):
        from graea.interfaces.cli import app
        result = self._runner().invoke(app, ["step", "send", "hello"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["action"]["text"] == "hello"

    def test_history_command(self, patched_interfaces):
        from graea.interfaces.cli import app
        result = self._runner().invoke(app, ["history", "demo"])
        assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class TestHTTP:
    def test_status(self, patched_interfaces):
        from fastapi.testclient import TestClient
        from graea.interfaces.http import app
        with TestClient(app) as client:
            resp = client.get("/status")
            assert resp.status_code == 200
            assert resp.json()["mtproto_connected"] is True

    def test_step_send(self, patched_interfaces):
        from fastapi.testclient import TestClient
        from graea.interfaces.http import app
        with TestClient(app) as client:
            resp = client.post("/step/send", json={"text": "hello"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["action"]["text"] == "hello"
            assert "raw" not in json.dumps(data["replies"])

    def test_step_send_with_expect(self, patched_interfaces):
        from fastapi.testclient import TestClient
        from graea.interfaces.http import app
        with TestClient(app) as client:
            resp = client.post("/step/send", json={
                "text": "hello",
                "expect": [{"kind": "text_contains", "value": "FAIL"}],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["assertions"][0]["passed"] is False

    def test_scenario_run(self, patched_interfaces, monkeypatch):
        import graea.interfaces.http as http_iface
        from graea.models import Scenario, ScenarioStep, Action, ActionKind
        fake_scenario = Scenario(name="demo", steps=[ScenarioStep(name="s1", action=Action(kind=ActionKind.send_text, text="hi"))])
        monkeypatch.setattr(http_iface, "load_scenario", lambda p: fake_scenario)

        from fastapi.testclient import TestClient
        with TestClient(http_iface.app) as client:
            resp = client.post("/scenario/run", json={"path_or_name": "demo"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["scenario"] == "demo"
            assert len(data["step_results"]) == 4

    def test_sql_error_is_200(self, patched_interfaces):
        from fastapi.testclient import TestClient
        from graea.interfaces.http import app
        with TestClient(app) as client:
            resp = client.post("/sql", json={"query": "DROP TABLE runs"})
            assert resp.status_code == 200
            assert "error" in resp.json()

    def test_diff_endpoint(self, patched_interfaces):
        from fastapi.testclient import TestClient
        from graea.interfaces.http import app
        with TestClient(app) as client:
            resp = client.get("/diff", params={"scenario": "demo"})
            assert resp.status_code == 200
            assert resp.json()["scenario"] == "demo"

    def test_shots_serves_png(self, patched_interfaces, fake_session):
        from fastapi.testclient import TestClient
        from graea.interfaces.http import app
        with TestClient(app) as client:
            # write a screenshot in the expected run_id/file layout
            shot_dir = fake_session._shots_dir / "r0001"
            shot_dir.mkdir(parents=True, exist_ok=True)
            (shot_dir / "step0.png").write_bytes(
                (fake_session._shots_dir / "step0.png").read_bytes()
                if (fake_session._shots_dir / "step0.png").exists()
                else b"\x89PNG\r\n\x1a\n"
            )
            resp = client.get("/shots/r0001/step0.png")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/png"

    def test_shots_404(self, patched_interfaces):
        from fastapi.testclient import TestClient
        from graea.interfaces.http import app
        with TestClient(app) as client:
            resp = client.get("/shots/nope/nope.png")
            assert resp.status_code == 404
