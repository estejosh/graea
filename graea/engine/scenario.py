"""YAML scenario loading.

Public API:
    scenarios_dir() -> Path                         -- graea/demo/scenarios
    scenario_from_dict(data: dict) -> Scenario
    load_scenario(path_or_name: str | Path) -> Scenario
        -- accepts an actual path to a YAML file, or a bare name (with or
           without ".yaml") resolved against scenarios_dir().
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import yaml

from graea.models import Scenario

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # graea/


def scenarios_dir() -> Path:
    """Directory holding the bundled demo scenarios (graea/demo/scenarios)."""
    return _PACKAGE_ROOT / "demo" / "scenarios"


def scenario_from_dict(data: dict) -> Scenario:
    """Validate a raw dict (already yaml.safe_load'd) into a Scenario."""
    return Scenario.model_validate(data)


def load_scenario(path_or_name: Union[str, Path]) -> Scenario:
    """Load a Scenario from a YAML file.

    `path_or_name` may be:
      - a path (absolute or relative to cwd) to an existing .yaml/.yml file
      - a bare scenario name (e.g. "smoke" or "smoke.yaml"), resolved against
        `scenarios_dir()` (graea/demo/scenarios/<name>.yaml)

    Raises FileNotFoundError if neither resolves to an existing file.
    """
    candidate = Path(path_or_name)
    if candidate.is_file():
        target = candidate
    else:
        name = str(path_or_name)
        if not name.endswith((".yaml", ".yml")):
            name = f"{name}.yaml"
        target = scenarios_dir() / name
        if not target.is_file():
            raise FileNotFoundError(
                f"scenario not found: {path_or_name!r} "
                f"(tried {candidate} and {target})"
            )
    raw = yaml.safe_load(target.read_text())
    return scenario_from_dict(raw)
