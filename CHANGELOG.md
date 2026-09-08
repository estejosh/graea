# Changelog

All notable changes to Graea are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [0.1.4] — 2026-09-08

Web login on the current Telegram Web K (field report + patch from a real
rootless-podman install).

- Telegram Web K now lands on the phone-number form; `login-web` clicks
  through to "Log in by QR code" when no QR is visible.
- The QR is drawn on a canvas that stays blank behind a preloader until
  Telegram issues a login token (and stays blank when it refuses one).
  `login-web` now waits up to 20s for the canvas to contain both dark and
  light opaque pixels, screenshots the canvas tightly, and prints
  `GRAEA_LOGIN_WEB: warning — QR canvas is blank` instead of handing over a
  spinner. (A never-drawn canvas is transparent, which a naive dark-pixel
  count mistakes for a code; alpha is checked.)
- Docs: headless + screenshot-the-QR is the reliable path (headed mode
  against Xwayland hung on navigation); IPv6-only resolvers may need
  `--disable-ipv6` / an IPv4 preference in the container.

## [0.1.3] — 2026-09-07

Visual eye against the redesigned Telegram Web K (field report: "visual eye
broken, structural eye fine").

- `is_logged_in()` no longer trusts a bare `#column-left` (it exists, hidden,
  on the QR screen). Logged-out is detected via `body.has-auth-pages` /
  visible `#auth-pages`; logged-in requires a visible left column or chat
  rows. `status --json` and `login-web` now agree; `login-web` shows the QR
  instead of "already".
- SELECTORS rewritten for WebK's CSS-module hashed class names: stable ids
  (`#auth-pages`, `#column-center`, `#chatlist-container`), class-substring
  patterns (`[class*='qrContainer']`, `[class*='_bubble_']`) and WebK's
  `data-mid` message attribute. Login-screen anchors verified against a live
  DOM dump; post-login bubble selectors are best-effort patterns.
- Element lookups wait for `visible`, not `attached`.
- Screenshots never fail: last messages → chat column → viewport fallback
  chain; `Screenshot.fallback` says which one was used and the step gets a
  note.
- New `graea web-probe`: dumps logged-in verdict, per-selector match/visible
  counts, a visible-element tree and a viewport PNG, for field patching.
- New `GRAEA_SELECTORS_FILE` (JSON `{role: [css, ...]}`) overrides selectors
  without a code change.

## [0.1.2] — 2026-09-07

Fixes from a first-run field report from an unattended Cline agent running
on Ubuntu with rootless podman:

- **Rootless podman bind mount (blocker)**: a fresh `./data` owned by the
  host user was not writable from inside the container, because rootless
  podman maps the container's uid 1000 through `/etc/subuid` to an
  unrelated host uid. Every documented `podman run` example now passes
  `--userns=keep-id` (install.sh, AGENTS.md, README.md, docs/SETUP.md,
  graea.agent.json, and `userns_mode: keep-id` in compose.yaml).
  `install.sh` now verifies `./data` is writable from inside the container
  before running `doctor`, falling back to `chmod -R a+rwX ./data` and
  printing `GRAEA_INSTALL: data dir writable: yes|repaired|NO`. `doctor`'s
  `data dir not writable` problem now names the exact fix
  (`--userns=keep-id` or `chmod -R a+rwX ./data`), aware of whether it's
  running inside the container. The Containerfile's comment explaining uid
  1000 was misleading; it now explains `--userns=keep-id` and adds
  `chmod 0777 /data` as a belt-and-braces fallback.
- **Telethon auth errors no longer print a Rich traceback**: `graea login`
  / `login-web` / `session export` now catch `telethon.errors.RPCError`,
  `RuntimeError`, and `OSError` and print one line —
  `GRAEA_LOGIN: error <ExceptionClassName> — <hint>` — then exit 1, with a
  hint mapped per error (bad phone format, wrong/expired code, missing 2FA
  password, bad api_id/api_hash, Telegram rate limit). The CLI now runs
  with `pretty_exceptions_enable=False` and every command is wrapped by a
  top-level guard that turns any other unexpected exception into
  `GRAEA_ERROR: <Class>: <msg>` (exit 1) instead of a traceback.
- **`doctor` now validates values, not just presence**: flags a still-default
  placeholder `GRAEA_BOT` (`@your_bot`, `your_bot`, `changeme`, `xxx`, or an
  angle-bracket placeholder), a missing/too-short `GRAEA_API_HASH`, and a
  missing `GRAEA_PHONE` (skipped when `GRAEA_SESSION_STRING` is already
  set). Adds a `config` object to the JSON output summarizing
  set/missing/suspicious/placeholder status for each credential — never the
  actual values.
- **AGENTS.md / graea.agent.json**: call out that the first container build
  downloads ~400 MB and takes several minutes, and that an unattended agent
  should run it in the background and tail the log
  (`nohup bash install.sh > install.log 2>&1 &`); `install.sh` prints this
  before building.
- **Minor**: `GRAEA_CONNECT_TIMEOUT_S` (default 20s) caps the first MTProto
  connect so `status --json` fails fast with a clear hint instead of
  hanging past 30s on a slow first connect. `graea login` / `login-web`
  print both the container path and the host-side `./data` equivalent for
  the code file / QR screenshot when `GRAEA_IN_CONTAINER=1`. `install.sh`
  only prints its "done, next steps" footer when `doctor` exits 0; on
  failure it prints the doctor command to re-run after fixing `.env`.

## [0.1.1] — 2026-09-07

- License changed from MIT to Elastic License 2.0 (source-available). No code changes.

## [0.1.0] — 2026-09-07

Initial release. Give any LLM eyes on a Telegram bot it built: MTProto
(structural eye) + Telegram Web via Playwright (visual eye) + a pluggable
vision reader, with every run/step/observation/assertion recorded in DuckDB
so an LLM can diff progress across runs instead of guessing.

Built as separate, contract-bound modules, each owned by its own build
agent and its own `docs/dev/agent-reports/<agent>.md` — see that directory for
what's untested / needs real credentials:

- `graea/client/` — Telethon (MTProto) transport: send text/commands/files,
  press buttons, observe new/edit/delete events, session file <->
  StringSession.
- `graea/visual/` — Playwright driving Telegram Web: login (QR), screenshots,
  and the pluggable vision reader (`openai_compatible` / `anthropic` / `ocr`
  / `none` / `caller` — the driving LLM reads the screenshot itself).
- `graea/engine/` — `TestSession`, the single orchestrator every interface
  drives: act -> wait -> observe -> screenshot -> read -> store -> diff.
  Also scenario loading (YAML), assertion evaluation, fingerprinting, and
  run/step diffing.
- `graea/store/` — DuckDB-backed persistence for runs, steps, observations,
  screenshots, vision readings, and assertions.
- `graea/interfaces/` — the three ways to drive Graea: `graea` CLI (typer),
  `graea serve` (FastAPI/HTTP), and `graea mcp` (MCP server over stdio,
  the recommended way for an LLM to drive this).
- `graea/demo/` — a demo bot with plantable bugs (`raw_markdown`,
  `truncated_button`, `edit_as_new`, `missing_caption`, `silent_command`,
  `dead_button`, `wrong_keyboard_shape`) and matching scenarios, for
  exercising the whole pipeline without a real bot.
- `graea/version.py` — self-update check (GitHub releases, 24h cache) and
  `graea update`/`graea_update_check`.

**Caller-reader mode**: the default vision provider is `caller` — no second
vision model is required at all. Every step returns its screenshot as both a
compact JSON `StepResult` and an image; the LLM driving Graea (via MCP) looks
at that image itself and reports back with `graea_submit_reading`. This is
how Graea is meant to be used out of the box; `openai_compatible` /
`anthropic` / `ocr` exist for headless/CI use where no vision-capable caller
is present.
