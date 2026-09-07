"""Every YAML file in graea/demo/scenarios must parse into models.Scenario.

This is a standalone smoke test: it does not use the real scenario loader
(owned elsewhere) — just yaml.safe_load + Scenario.model_validate, per
docs/dev/AGENT-RULES.md ("only create/edit the files assigned to you").
"""
from __future__ import annotations

from pathlib import Path

import yaml

from graea.models import Scenario

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "graea" / "demo" / "scenarios"


def _scenario_files() -> list[Path]:
    files = sorted(SCENARIOS_DIR.glob("*.yaml"))
    assert files, f"no scenario YAML files found under {SCENARIOS_DIR}"
    return files


def test_all_scenario_files_parse():
    for path in _scenario_files():
        raw = yaml.safe_load(path.read_text())
        scenario = Scenario.model_validate(raw)
        assert scenario.name, f"{path} has no name"
        assert scenario.steps, f"{path} has no steps"
        assert scenario.source_path == "graea/demo", path


def test_expected_scenarios_present():
    names = {p.stem for p in _scenario_files()}
    expected = {
        "smoke",
        "start_markdown",
        "truncated_button",
        "order_edit",
        "missing_caption",
        "silent_help",
        "dead_button",
        "wrong_keyboard_shape",
    }
    assert expected <= names


def test_smoke_scenario_covers_happy_path():
    smoke = Scenario.model_validate(
        yaml.safe_load((SCENARIOS_DIR / "smoke.yaml").read_text())
    )
    step_names = [s.name for s in smoke.steps]
    assert "start" in step_names
    assert "press_order" in step_names
    assert "help" in step_names
    assert "menu" in step_names
    assert len(smoke.steps) >= 5
