"""Graea's own version, and a best-effort "is there a newer release?" check.

Never raises, never blocks longer than ~3s, and is disabled outright by
`settings.check_updates=False` or `GRAEA_OFFLINE=1`. Results are cached on
disk (next to the DuckDB file) for 24h so agents/CLIs that call this on
every invocation don't hit the network every time.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from importlib import metadata as _metadata
from pathlib import Path
from typing import Optional

import httpx

from graea.models import UpdateInfo, utcnow

try:
    __version__ = _metadata.version("graea")
except _metadata.PackageNotFoundError:  # pragma: no cover - only when not installed at all
    __version__ = "0.0.0+unknown"

REPO = "estejosh/graea"
_CACHE_TTL = timedelta(hours=24)
_HTTP_TIMEOUT = 3.0


# --------------------------------------------------------------------------
# semver
# --------------------------------------------------------------------------


def _parse_semver(version: str) -> tuple[int, int, int]:
    v = version.strip()
    if v.startswith(("v", "V")):
        v = v[1:]
    core = re.split(r"[-+]", v, maxsplit=1)[0]
    parts = core.split(".")
    nums: list[int] = []
    for part in parts[:3]:
        m = re.match(r"\d+", part)
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def is_newer(latest: str, current: str) -> bool:
    """True if `latest` is a strictly newer semver than `current` (leading 'v' ignored)."""
    try:
        return _parse_semver(latest) > _parse_semver(current)
    except Exception:
        return False


# --------------------------------------------------------------------------
# install-mode detection
# --------------------------------------------------------------------------


def repo_root() -> Optional[Path]:
    """Best-effort repo root for editable installs: the directory containing
    this package that also has a sibling pyproject.toml and .git."""
    try:
        pkg_dir = Path(__file__).resolve().parent  # .../graea (the package)
        candidate = pkg_dir.parent  # repo root, if this is an editable/clone install
        if (candidate / "pyproject.toml").exists() and (candidate / ".git").exists():
            return candidate
    except Exception:
        pass
    return None


def detect_install_mode() -> str:
    """"container" | "editable" | "pip" — how this graea was installed."""
    if os.environ.get("GRAEA_IN_CONTAINER") == "1":
        return "container"
    if repo_root() is not None:
        return "editable"
    return "pip"


def how_to_update(mode: Optional[str] = None) -> list[str]:
    """Commands a human/agent should run to update, for the given (or detected) install mode."""
    mode = mode or detect_install_mode()
    if mode == "container":
        return [
            "podman pull ghcr.io/estejosh/graea:latest",
            "cd <repo> && git pull && podman build -t graea:latest -f Containerfile .",
        ]
    if mode == "editable":
        root = repo_root()
        repo_str = str(root) if root else "<repo>"
        return [
            f"cd {repo_str}",
            "git pull --ff-only",
            ".venv/bin/pip install -e .  (or pip install -e .)",
            "playwright install chromium",
        ]
    return ["pip install -U git+https://github.com/estejosh/graea"]


# --------------------------------------------------------------------------
# GitHub lookup
# --------------------------------------------------------------------------


def _fetch_latest_tag() -> tuple[Optional[str], Optional[str]]:
    """(tag, html_url) of the latest release, falling back to the newest git tag.
    Returns (None, None) on any failure (never raises)."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "graea-update-check"}
    base = f"https://api.github.com/repos/{REPO}"
    try:
        resp = httpx.get(f"{base}/releases/latest", timeout=_HTTP_TIMEOUT, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            tag = data.get("tag_name")
            if tag:
                return tag, data.get("html_url")
    except Exception:
        pass
    try:
        resp = httpx.get(f"{base}/tags", timeout=_HTTP_TIMEOUT, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                tag = data[0].get("name")
                if tag:
                    return tag, f"https://github.com/{REPO}/releases/tag/{tag}"
    except Exception:
        pass
    return None, None


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------


def _cache_path(settings) -> Path:
    return Path(settings.db).parent / "update-check.json"


def _read_cache(path: Path, now: datetime) -> Optional[UpdateInfo]:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        info = UpdateInfo.model_validate(data)
    except Exception:
        return None
    if info.current != __version__:
        return None  # graea was updated since this was cached; stale
    checked_at = info.checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    if now - checked_at > _CACHE_TTL:
        return None
    return info


def _write_cache(path: Path, info: UpdateInfo) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(info.model_dump_json())
    except Exception:
        pass  # caching is an optimization, never fatal


def _build_info(latest_tag: str, html_url: Optional[str], now: datetime) -> UpdateInfo:
    current = __version__
    latest = latest_tag[1:] if latest_tag.lower().startswith("v") else latest_tag
    update_available = is_newer(latest_tag, current)
    return UpdateInfo(
        current=current,
        latest=latest,
        update_available=update_available,
        html_url=html_url,
        how_to_update=how_to_update() if update_available else [],
        checked_at=now,
    )


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def check_for_update(settings, force: bool = False) -> Optional[UpdateInfo]:
    """Check GitHub for a newer graea release than the one currently installed.

    Returns `None` (never raises) when: `settings.check_updates` is False,
    `GRAEA_OFFLINE=1` is set, the network call fails/times out (3s cap), or
    no release/tag could be found at all. Otherwise returns an `UpdateInfo`
    (which may have `update_available=False`). Results are cached on disk
    for 24h; pass `force=True` to bypass the cache and re-check now.
    """
    try:
        if os.environ.get("GRAEA_OFFLINE") == "1":
            return None
        if not getattr(settings, "check_updates", True):
            return None

        cache_path = _cache_path(settings)
        now = utcnow()

        if not force:
            cached = _read_cache(cache_path, now)
            if cached is not None:
                return cached

        latest_tag, html_url = _fetch_latest_tag()
        if not latest_tag:
            return None

        info = _build_info(latest_tag, html_url, now)
        _write_cache(cache_path, info)
        return info
    except Exception:
        return None
