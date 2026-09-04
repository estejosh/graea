"""Fingerprint the bot's source so regressions can be pinned to a change.

Public API:
    fingerprint(source_path) -> str | None
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional, Union

_SOURCE_EXTS = {".py", ".js", ".ts", ".json", ".yaml", ".toml"}


def _run_git(args: list[str], cwd: Path) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _is_git_repo(path: Path) -> bool:
    top = _run_git(["rev-parse", "--show-toplevel"], path)
    return bool(top)


def _tree_hash(path: Path) -> str:
    entries: list[tuple[str, bytes]] = []
    for f in sorted(path.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix not in _SOURCE_EXTS:
            continue
        try:
            content = f.read_bytes()
        except OSError:
            continue
        rel = str(f.relative_to(path))
        entries.append((rel, content))
    entries.sort(key=lambda e: e[0])
    h = hashlib.sha256()
    for rel, content in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(content)
        h.update(b"\0")
    return "tree:" + h.hexdigest()[:12]


def fingerprint(source_path: Union[str, Path, None]) -> Optional[str]:
    """Return a short identifier for the bot's source tree.

    If source_path lives inside a git repo, returns the short commit hash of
    HEAD, suffixed "-dirty" if there are uncommitted changes. Otherwise,
    returns "tree:<sha256[:12]>" of the sorted (relpath, content) pairs of
    *.py/*.js/*.ts/*.json/*.yaml/*.toml files under source_path. Returns None
    if source_path is None or does not exist. Never raises.
    """
    if source_path is None:
        return None
    path = Path(source_path)
    if not path.exists():
        return None
    try:
        if path.is_file():
            base = path.parent
        else:
            base = path

        if _is_git_repo(base):
            short_hash = _run_git(["rev-parse", "--short", "HEAD"], base)
            if short_hash:
                status = _run_git(["status", "--porcelain"], base)
                dirty = bool(status)
                return f"{short_hash}-dirty" if dirty else short_hash

        return _tree_hash(base)
    except Exception:
        return None
