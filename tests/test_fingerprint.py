"""Tests for graea.engine.fingerprint.fingerprint."""
from __future__ import annotations

import subprocess


from graea.engine.fingerprint import fingerprint


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_fingerprint_none_for_missing_path(tmp_path):
    assert fingerprint(None) is None
    assert fingerprint(tmp_path / "does-not-exist") is None


def test_fingerprint_git_repo_clean(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "bot.py").write_text("print('hi')\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "init"], repo)

    fp = fingerprint(repo)
    assert fp is not None
    assert not fp.endswith("-dirty")
    assert not fp.startswith("tree:")


def test_fingerprint_git_repo_dirty(tmp_path):
    repo = tmp_path / "repo2"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "bot.py").write_text("print('hi')\n")
    _git(["add", "."], repo)
    _git(["commit", "-m", "init"], repo)

    (repo / "bot.py").write_text("print('changed')\n")
    fp = fingerprint(repo)
    assert fp is not None
    assert fp.endswith("-dirty")


def test_fingerprint_plain_dir_tree_hash(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "bot.py").write_text("a = 1\n")
    (d / "config.yaml").write_text("x: 1\n")
    (d / "ignored.txt").write_text("not source\n")

    fp = fingerprint(d)
    assert fp is not None
    assert fp.startswith("tree:")


def test_fingerprint_plain_dir_changes_when_content_changes(tmp_path):
    d = tmp_path / "plain2"
    d.mkdir()
    (d / "bot.py").write_text("a = 1\n")
    fp1 = fingerprint(d)

    (d / "bot.py").write_text("a = 2\n")
    fp2 = fingerprint(d)
    assert fp1 != fp2


def test_fingerprint_plain_dir_stable_ordering(tmp_path):
    d1 = tmp_path / "d1"
    d1.mkdir()
    (d1 / "b.py").write_text("1\n")
    (d1 / "a.py").write_text("2\n")

    d2 = tmp_path / "d2"
    d2.mkdir()
    (d2 / "a.py").write_text("2\n")
    (d2 / "b.py").write_text("1\n")

    assert fingerprint(d1) == fingerprint(d2)


def test_fingerprint_ignores_non_source_extensions(tmp_path):
    d = tmp_path / "d3"
    d.mkdir()
    (d / "bot.py").write_text("a = 1\n")
    fp1 = fingerprint(d)
    (d / "notes.txt").write_text("irrelevant\n")
    fp2 = fingerprint(d)
    assert fp1 == fp2


def test_fingerprint_never_raises_on_weird_input(tmp_path):
    # a file path (not a dir) inside a non-git plain tree
    f = tmp_path / "onefile.py"
    f.write_text("x = 1\n")
    fp = fingerprint(f)
    assert fp is None or isinstance(fp, str)
