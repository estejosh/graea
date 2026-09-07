# Update-checking / self-update — agent report

## Files added

- `graea/version.py` — `__version__` (via `importlib.metadata`, falls back to
  `"0.0.0+unknown"` when graea isn't pip-installed, e.g. this sandbox);
  `check_for_update(settings, force=False) -> UpdateInfo | None`
  (GitHub `releases/latest`, falling back to `/tags`; httpx, 3s timeout,
  24h on-disk cache at `<db dir>/update-check.json`, disabled by
  `settings.check_updates=False` or `GRAEA_OFFLINE=1`, never raises);
  `is_newer`, `detect_install_mode` (`container`/`editable`/`pip`),
  `repo_root`, `how_to_update`.
- `.github/workflows/ci.yml` — python 3.11, `pip install -e ".[dev,demo]"`,
  ruff, `playwright install --with-deps chromium`, tesseract via apt, pytest.
- `.github/workflows/release.yml` — on tag `v*`: `redhat-actions/buildah-build@v2`
  builds the `Containerfile` with podman, `redhat-actions/push-to-registry@v2`
  pushes `ghcr.io/estejosh/graea:latest` + `:<version>` using
  `GITHUB_TOKEN` (`packages: write`), then `softprops/action-gh-release@v2`
  creates a GitHub Release with auto-generated notes.
- `LICENSE` — MIT, © 2026 Joshua D. Hale.
- `CHANGELOG.md` — 0.1.0 entry (module list, caller-reader mode).
- `tests/test_version.py` — semver compare (incl. garbage input), cache
  hit/miss (mocked `httpx.get`), force-bypass, releases→tags fallback, stale
  cache refetch, disabled-by-settings, disabled-by-`GRAEA_OFFLINE`, network
  error → `None`, install-mode detection + `how_to_update` per mode,
  `graea version`, `graea update --check`, and `graea_update_check` +
  `graea_status` + step-`notes` banner via the MCP `FakeSession` pattern.

## Files changed

- `graea/models.py` — new `UpdateInfo` model; `Health` gains
  `version: Optional[str]` and `update: Optional[UpdateInfo]`.
- `graea/config.py` — `check_updates: bool = True` (env `GRAEA_CHECK_UPDATES`).
- `graea/interfaces/cli.py` — `app.callback()` prints
  `GRAEA_UPDATE: <cur> -> <latest> (run: graea update)` to **stderr** before
  every subcommand except `mcp`/`serve` (cheap: cache-first); `status`
  fills `health.version`/`health.update`; new `graea version` and
  `graea update [--check]` commands (subprocess-based apply per install
  mode, streams output, force-refreshes the cache).
- `graea/interfaces/mcp_server.py` — `get_session()` runs
  `check_for_update` once at session start and caches the result in a
  module-level `_cached_update` so later tool calls never touch the
  network; `_step_content()` appends an `"update available: ..."` line to
  every returned `StepResult.notes` when one is cached; `graea_status` adds
  `version`/`update`; new `graea_update_check` tool (forces a fresh check,
  returns `UpdateInfo` JSON).
- `Containerfile` — `ENV GRAEA_IN_CONTAINER=1` (drives install-mode
  detection to `container`).
- `install.sh` — container mode now tries
  `podman pull ghcr.io/estejosh/graea:latest` + `podman tag ... graea:latest`
  first, falls back to the existing local `podman build` on any failure;
  prints `GRAEA_INSTALL: image source: ghcr|local`.
- `graea.agent.json` — new `"update"` block (`check_cmd`/`update_cmd` per
  install mode, `graea_update_check` MCP tool name).
- `AGENTS.md` — new "Staying current" section: run `graea update --check`
  (or the MCP tool) at session start; the banner/notes/`status` fields
  surface it automatically; `graea update` applies it per install mode;
  container users get updates via `podman pull`.
- `pyproject.toml` — `[project.urls]` (Homepage, Repository, Changelog).
- `tests/conftest.py` — autouse fixture sets `GRAEA_OFFLINE=1` for every
  test so the new CLI banner / MCP session-start check never hit the
  network in the suite (tests that exercise `check_for_update` itself
  `monkeypatch.delenv` it back off).

## Verified

- `python -m pytest -q -p no:cacheprovider` — 201 passed.
- `ruff check .` — clean.
- Manual smoke: `graea version`, `graea update --check` (offline path),
  `detect_install_mode()`/`repo_root()`/`how_to_update()` against this
  checkout (correctly detects `"editable"` since `/home/claude/graea` has
  both `.git` and `pyproject.toml`).

## Not verified / known gaps

- **The two GitHub Actions workflows cannot run in this environment** (no
  CI runner, no `gh`/registry credentials, no network to GHCR). They're
  syntactically reviewed but untested end-to-end — in particular I could
  not confirm `redhat-actions/buildah-build@v2` + `redhat-actions/push-to-registry@v2`
  produce a pullable `ghcr.io/estejosh/graea:latest` on a real run, or that
  `softprops/action-gh-release@v2`'s auto-generated notes read well without
  a real tag/PR history.
- **`graea update`'s container-mode branch is effectively a no-op from
  inside a container** (a running container normally can't reach the
  host's podman) — this is called out explicitly in `AGENTS.md` and
  `graea.agent.json`; the intended path is running `podman pull` on the
  host (which `install.sh` now also does automatically on install/reinstall).
- **`__version__` is `"0.0.0+unknown"` in this sandbox** because `graea`
  isn't `pip install`-ed here (per `docs/dev/AGENT-RULES.md`, only already-
  installed packages may be used) — this exercises the documented fallback
  path correctly, but the real "current version" behavior can only be
  confirmed against an actual `pip install -e .`/built wheel, not here.
- The live GitHub API call (`api.github.com/repos/estejosh/graea/...`) was
  never exercised for real (no network in this sandbox) — only via mocked
  `httpx.get`. First real run against the actual repo (which currently has
  no releases/tags) should confirm the "no release/tag exists yet" ->
  `check_for_update` returns `None` path behaves as intended.
- `graea/interfaces/http.py` (`graea serve`) was intentionally left
  untouched — the task named CLI + MCP for wiring `version`/`update`, and
  the HTTP `/status` endpoint already returns whatever `Health` model the
  session produces, so it will surface `version`/`update` once a caller
  sets them, but nothing populates those fields there yet.
