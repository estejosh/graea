# Agent report: container / unattended install

## Goal

Let an LLM agent install and bring Graea to a working state from the git URL
with minimal human involvement — the only unavoidable human moments are the
Telegram phone-code (first MTProto login) and the QR scan (Telegram Web
login). Container runtime is **podman**, never docker.

## Files added

- `Containerfile` — `python:3.11-slim` base; installs `tesseract-ocr` +
  Playwright Chromium's Debian runtime deps; `pip install ".[demo]"` then
  `playwright install chromium` baked in at `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`;
  non-root `graea` user (uid 1000) for rootless podman; `WORKDIR /app`,
  `VOLUME /data`; default env points `GRAEA_SESSION`/`GRAEA_DB`/`GRAEA_SHOTS`/
  `GRAEA_WEB_PROFILE` at `/data/...`, `GRAEA_WEB_HEADLESS=true`; `ENTRYPOINT
  ["graea"]`, `CMD ["status", "--json"]`.
- `.containerignore` — excludes `data/`, `.git`, caches, venvs, tests/docs
  from the build context.
- `compose.yaml` — podman-compose compatible (no `version:` key). Services:
  `graea-mcp` (`command: mcp`, `stdin_open: true`, `./data:/data:Z`,
  `env_file: .env`), `graea-http` (`command: serve --host 0.0.0.0`, ports
  `8765:8765`), `demo-bot` (entrypoint override to
  `python -m graea.demo.buggy_bot`, env `DEMO_BOT_TOKEN`/`BUGS`).
- `install.sh` — idempotent, no prompts. Default mode: detects `podman`
  (fails loudly naming `podman`, not docker, if absent), `podman build -t
  graea:latest -f Containerfile .`, `mkdir -p data`, copies `.env.example`
  to `.env` only if `.env` is absent, runs `doctor` inside the built image,
  prints `GRAEA_INSTALL: ...` next steps. `--no-container` mode: `.venv`,
  `pip install -e ".[demo,dev]"`, `playwright install chromium`, `doctor`.
- `AGENTS.md` — the document an LLM reads to install Graea unattended: human
  prereqs, install command, both login handshakes written as a
  watch-stdout-for-a-line / respond-once protocol, verification commands,
  MCP registration JSON (container + venv), first test run against the demo
  bot, and a symptom → fix table.
- `graea.agent.json` — machine-readable mirror of `AGENTS.md` (install
  commands, MCP server configs, login protocol with the exact lines to
  watch for, verify commands, troubleshooting map).
- `tests/test_cli_noninteractive.py` — 8 tests, no credentials/network:
  `status --json` without creds (exit 2, valid JSON, `hint` set, both with
  and without `--json`), `doctor` JSON shape (`ok`/`problems`/
  `vision_reachable`, both failing and passing cases), `login
  --code-from-file` (code picked up from a file and the file deleted
  after, plus `GRAEA_LOGIN_CODE` env-var priority over the file), and
  `TelethonTransport` session selection (`StringSession` when
  `settings.session_string` is set, the file session otherwise).

## Files changed

- `graea/models.py` — added `Health.hint: Optional[str] = None` (the one
  permitted contract-file change named in the task; no other field
  touched).
- `graea/config.py` — added `Settings.session_string: Optional[str] = None`
  (env `GRAEA_SESSION_STRING`).
- `graea/client/mtproto.py` — `TelethonTransport.__init__` now builds a
  `telethon.sessions.StringSession(settings.session_string)` instead of the
  file-path session when `settings.session_string` is set (checked before
  falling back to `str(settings.session)`).
- `graea/interfaces/cli.py`:
  - `login`: non-interactive-safe. Code source priority: `GRAEA_LOGIN_CODE`
    env var → `--code-from-file PATH` (polled every 2s up to `--timeout`
    300s default, file deleted once read; defaults to
    `<session dir>/login-code.txt` when stdin isn't a TTY) → interactive
    `typer.prompt` (TTY only). 2FA password: `GRAEA_2FA_PASSWORD` env var,
    else TTY prompt. Prints `GRAEA_LOGIN: waiting for code, write it to
    <path>` and `GRAEA_LOGIN: ok <user>`.
  - `login-web`: writes the QR to `<shots dir>/../login-qr.png`
    (`/data/login-qr.png` in the container), prints `GRAEA_LOGIN_WEB: scan
    <path>`, re-screenshots + reprints that line every 20s (WebK QR codes
    rotate ~30s) while polling `wait_for_login` in chunks, prints
    `GRAEA_LOGIN_WEB: ok` on success or `GRAEA_LOGIN_WEB: already` (exit 0)
    if already logged in, `GRAEA_LOGIN_WEB: timed out` (exit 1) otherwise.
  - `session export` (new `session` sub-typer): connects with the existing
    file session and prints `StringSession.save(client.session)`, for a
    human to log in once and an agent to inject `GRAEA_SESSION_STRING`
    into any container thereafter with zero further human steps. Errors
    clearly (exit 1) if `api_id`/`api_hash` or the session file are
    missing/unauthorized.
  - `status`: added `--json`. Rewritten to call `session.start()` directly
    (its return value **is** the Health snapshot per `TestSession.start()`'s
    own docstring) inside a try/except — any exception (e.g. the
    unauthorized-session `RuntimeError` from `TelethonTransport.connect()`)
    is caught and turned into `Health(mtproto_connected=False,
    hint=str(exc), db_path=..., bot=...)` instead of propagating. Exit code
    0 if `mtproto_connected`, else 2, in both `--json` and table modes.
  - `doctor` (new): offline-only JSON report — Python version, `tesseract`
    on PATH, Playwright Chromium found under `PLAYWRIGHT_BROWSERS_PATH` (or
    the default `~/.cache/ms-playwright`), `GRAEA_API_ID`/`GRAEA_API_HASH`/
    `GRAEA_BOT` set, data dir writable (write+delete a probe file). A
    best-effort `GET {vision_base_url}/models` (3s timeout) is reported as
    `vision_reachable` but never added to `problems` / never fails the
    check, per spec ("report but don't fail"). Prints `{"ok", "problems",
    "vision_reachable"}`, exit 0/1.
  - `serve`: added `--host`/`--port` options (override
    `settings.http_host`/`http_port` before calling `http_iface.serve`);
    `graea.interfaces.http.serve()` already accepted a `Settings` and used
    `settings.http_host`/`http_port`, so no change was needed there.
- `README.md` — quick start now leads with `./install.sh`, links
  `AGENTS.md`, keeps the manual `pip install -e` path below it as a
  fallback; added `GRAEA_SESSION_STRING` to the env var table.
- `docs/SETUP.md` — added a "Container (podman)" section: build/run/login/
  verify/serve commands, `install.sh`/`compose.yaml` pointers, and what
  `/data` needs to be bind-mounted for (state persistence across runs).

## Verification

- `python -m pytest -q -p no:cacheprovider`: **176 passed** (168 pre-existing
  + 8 new), no network, no credentials.
- `ruff check .`: clean.
- Manually exercised (no real Telegram creds available in this environment):
  `graea doctor` and `graea status --json` both print valid JSON and exit
  1/2 respectively instead of tracebacking with no `.env` configured;
  `graea --help`, `graea login --help`, `graea login-web --help` render
  correctly with the new options/docstrings.
- `Containerfile` was **not** build-tested — no working container daemon in
  this environment (`docker info` fails: no docker socket; podman is not
  installed here either). `docker build -f Containerfile .` was attempted
  as the allowed syntax-check fallback and failed only on daemon
  connectivity, not Containerfile content. Package versions in
  `pyproject.toml` and the apt package list were sanity-checked by hand
  against Playwright's documented Debian/Chromium dependency list.

## Known gaps / untested without real credentials or a container runtime

- The actual `podman build`, `install.sh` (container path), `compose.yaml`
  service startup, `login`/`login-web` against real Telegram, and
  `session export` round-trip are unverified end-to-end in this
  environment — they're covered by unit tests against fakes
  (`test_cli_noninteractive.py`) plus manual smoke checks of the
  no-creds/offline code paths, but not run against a live podman + real
  Telegram account.
- `install.sh --no-container` was syntax-checked (`bash -n`) and its logic
  reviewed but not run to completion here (would install/upgrade packages
  and run `playwright install chromium`, which the base AGENT-RULES.md for
  this repo's other agents asks build agents not to do in this shared dev
  environment).
