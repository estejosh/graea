# AGENTS.md — unattended install for Graea

This is the document an LLM agent (Claude Code, Codex, etc.) reads to install
and bring Graea to a working state from the git URL with as little human
involvement as possible. A machine-readable mirror of this file is at
`graea.agent.json` — parse that instead of this prose if you prefer.

There are exactly two moments that need a human, and both are one-shot:

1. The Telegram **phone-code** on first MTProto login (`graea login`) — skip
   entirely if the human already has a `GRAEA_SESSION_STRING` from a prior
   login (see "Session export" below).
2. Scanning a **QR code** for Telegram Web login (`graea login-web`).

Everything else — build, config, verification, running scenarios — is
non-interactive and idempotent. Do not ask the human anything not listed
above.

## 0. What to get from the human before starting

Ask for these once, up front:

- `api_id`, `api_hash` — from <https://my.telegram.org> ("API development
  tools"), for a **spare** Telegram account (not the human's personal one).
- `phone` — that spare account's phone number. **Not needed** if the human
  already has a `GRAEA_SESSION_STRING` for this account.
- `bot` — the `@username` of the bot to test against.
- Optionally: an existing `GRAEA_SESSION_STRING` (see below) — if they have
  one, the phone-code step (#1 above) is skipped entirely.

## 1. Install

```bash
git clone https://github.com/estejosh/graea && cd graea && bash install.sh
```

**The first build downloads ~400 MB** (Chromium, fonts, apt deps, wheels)
and takes several minutes. An unattended agent must run it in the
background and tail the log — a killed build loses the in-flight layer and
has to restart from wherever podman's cache left off:

```bash
nohup bash install.sh > install.log 2>&1 &
tail -f install.log
# wait for the line: GRAEA_INSTALL: done.
```

This builds the podman image (`Containerfile` — Python 3.11, tesseract,
Playwright Chromium baked in), creates `./data`, copies `.env.example` to
`.env` if it doesn't already exist, verifies (and if needed repairs) that
`./data` is writable from inside the container, and runs `graea doctor` to
sanity-check the environment. Safe to re-run.

**Rootless podman note:** every container invocation below passes
`--userns=keep-id`. Without it, the container's uid (1000) maps through
`/etc/subuid` to an unrelated host uid, so a host-owned `./data` is not
writable from inside the container and `doctor`/real runs fail with
`data dir not writable (/data): Permission denied`. `--userns=keep-id` makes
the container user's uid equal your host uid, so the bind mount just works.
`install.sh` also runs a one-time write-test and falls back to
`chmod -R a+rwX ./data` if needed, printing
`GRAEA_INSTALL: data dir writable: yes|repaired|NO`.

No podman on this machine? Use the venv path instead:

```bash
./install.sh --no-container
```

This sets up `.venv`, `pip install -e ".[demo,dev]"`, `playwright install
chromium`, then runs `graea doctor`.

Either way, put the human's `api_id`/`api_hash`/`bot` (and
`GRAEA_SESSION_STRING` if they gave you one) into `.env` before continuing.

**Container runtime is podman, never docker.** Commands are `podman
build`/`podman run`, the image file is `Containerfile`. If `podman` isn't on
PATH, `install.sh` fails loudly naming `podman` — don't substitute `docker`.

Expect `install.sh` to exit non-zero on a fresh clone: `doctor` runs at the
end and reports the empty/placeholder credentials in `.env` as `problems`.
That is the signal to fill `.env`, then re-run the doctor command it prints.
Exit 0 from `install.sh` means the image, the data mount, and `.env` are all
good.

## 2. The two login handshakes

Skip both entirely if `.env` already has a working `GRAEA_SESSION_STRING`
(mtproto) and a Playwright profile at `GRAEA_WEB_PROFILE` that's already
logged in (web) — check with `graea status --json` first (step 4).

### 2a. MTProto (phone-code) — `graea login`

Run it in the **background** so you can watch its stdout while it blocks:

```bash
podman run -it --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest login
# or, venv mode:
.venv/bin/graea login
```

Protocol:

1. Watch stdout for the line `GRAEA_LOGIN: waiting for code, write it to <path>`.
   Inside the container this line prints **both** paths —
   `GRAEA_LOGIN: waiting for code, write it to /data/login-code.txt
   (container) = ./data/login-code.txt on the host` — the prefix
   `GRAEA_LOGIN: waiting for code, write it to ` stays stable; the first
   path right after it is always the one to parse/write from the host side.
2. Ask the human for the login code **once** ("Telegram just sent your spare
   account a login code — what is it?").
3. Write the code to `<path>` exactly as named in that line (e.g. `echo
   123456 > data/login-code.txt` for the container case, since `./data` is
   bind-mounted to `/data`). The command polls that file every 2s for up to
   300s and deletes it once read.
4. Wait for `GRAEA_LOGIN: ok <user>`. That's success — nothing else to do.
   (If the account has 2FA, set `GRAEA_2FA_PASSWORD` in `.env` beforehand so
   the command never needs to prompt for it.)

If the phone/code/2FA is rejected by Telegram (bad phone format, wrong or
expired code, missing 2FA password, bad api_id/api_hash, rate limiting),
`graea login` prints one line — `GRAEA_LOGIN: error <ExceptionClassName> —
<hint>` — and exits 1. No traceback; the hint says what to fix (e.g. re-run
with a corrected `GRAEA_PHONE`, or re-run login for a fresh code).

### 2b. Telegram Web (QR) — `graea login-web`

```bash
podman run -it --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest login-web
# or, venv mode:
.venv/bin/graea login-web
```

Protocol:

1. Watch stdout for `GRAEA_LOGIN_WEB: scan <path>` (e.g.
   `data/login-qr.png`). Inside the container this also prints both paths,
   same as `graea login` above (`... scan /data/login-qr.png (container) =
   ./data/login-qr.png on the host`).
2. Send the human that PNG (it's a file on disk — attach it, don't try to
   describe it) and ask them to scan it with the Telegram app on the spare
   account: **Settings > Devices > Link Desktop Device**.
3. The QR rotates roughly every 30s; the command re-screenshots the same
   path every 20s and reprints the `scan` line — if the human is slow, just
   keep pointing them at the (refreshed) same file.
4. Wait for `GRAEA_LOGIN_WEB: ok`. If it prints `GRAEA_LOGIN_WEB: already`
   instead, the web session was already logged in — also success.

### Session export — do this once, reuse everywhere

After `graea login` succeeds once (anywhere), export a portable
`StringSession` so future containers/environments skip the phone-code step
entirely:

```bash
podman run --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest session export
```

Put the printed string into `GRAEA_SESSION_STRING` in `.env` (or inject it
directly into a container's environment). `TelethonTransport` prefers
`GRAEA_SESSION_STRING` over the `GRAEA_SESSION` file when both are present.
This does **not** replace the web (QR) login — Telegram Web keeps its own
session in the `GRAEA_WEB_PROFILE` browser profile directory, so hand that
whole directory forward (bind-mount it) rather than trying to export it.

## 3. Verify

```bash
podman run --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest status --json
podman run --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest doctor
```

`status --json` exits 0 when the MTProto session is connected and
authorized, 2 otherwise (check the `hint` field for why — it never
tracebacks even with no credentials at all). **The first MTProto connect can
be slow** (well past 30s on a cold start); `status` caps it at
`GRAEA_CONNECT_TIMEOUT_S` (default 20s) and exits 2 with
`hint: "Telegram connect timed out after 20s (first connect can be slow;
re-run)"` rather than hanging — just re-run it. `doctor` runs offline checks
(Python version, tesseract, Playwright Chromium, required env vars *and*
values — a placeholder `GRAEA_BOT`, an empty/too-short `GRAEA_API_HASH`,
etc. are called out individually — data dir writability, a best-effort
vision-endpoint probe) and prints
`{"ok": bool, "problems": [...], "vision_reachable": ..., "config": {...}}`
(the `config` object never echoes the actual api_hash/phone, only
set/missing/suspicious/placeholder), exiting 0/1.

## 4. Register the MCP server

Container:

```json
{
  "mcpServers": {
    "graea": {
      "command": "podman",
      "args": ["run", "-i", "--rm", "--userns=keep-id", "-v", "./data:/data:Z", "--env-file", ".env", "graea:latest", "mcp"]
    }
  }
}
```

venv:

```json
{
  "mcpServers": {
    "graea": {
      "command": ".venv/bin/graea",
      "args": ["mcp"]
    }
  }
}
```

See `docs/LLM-GUIDE.md` for the tool-calling loop once it's registered
(`graea_start_run` → act → check assertions/vision/diff → fix → rerun).

## 5. First test run — the demo bot

Confirms the whole pipeline end to end without touching the human's real
bot:

```bash
# demo bot's own token, from @BotFather — separate from the test-user creds above
export DEMO_BOT_TOKEN=123456:ABC...
BUGS=raw_markdown python -m graea.demo.buggy_bot &   # or: podman-compose run --rm demo-bot

export GRAEA_BOT=@your_demo_bot
.venv/bin/graea run graea/demo/scenarios/start_markdown.yaml
```

`BUGS=` unset/empty makes the demo bot behave correctly (should pass
`graea/demo/scenarios/smoke.yaml`); each other scenario under
`graea/demo/scenarios/` targets one specific planted bug.

## 6. When something fails

| Symptom | Fix |
|---|---|
| `doctor` reports `data dir not writable (/data): Permission denied` | Rootless podman: container uid maps through `/etc/subuid` to a host uid that doesn't own your host `./data`. Run the container with `--userns=keep-id` (see "Rootless podman note" in §1), or on the host: `chmod -R a+rwX ./data`. |
| `status --json` hint mentions "not authorized" / "session ... " | The MTProto session was never logged in, or belongs to a different `api_id`/`api_hash`. Redo §2a, or fetch a fresh `GRAEA_SESSION_STRING`. |
| `status --json` shows `web_logged_in: false` | Redo §2b. Make sure `GRAEA_WEB_PROFILE` is a persistent, writable, bind-mounted directory — not an ephemeral container path that resets every run. |
| `vision.error` says `pending` | You are the reader (`GRAEA_VISION_PROVIDER=caller`, the default). Look at the screenshot image the tool returned and call `graea_submit_reading(description, issues)`. |
| `doctor`'s `vision_reachable` is `false` | Point `GRAEA_VISION_BASE_URL` at a running OpenAI-compatible endpoint (Ollama's default is `http://localhost:11434/v1` on the host; from inside the container use `http://host.containers.internal:11434/v1`), or set `GRAEA_VISION_PROVIDER=ocr` (tesseract-only) or `=none` (skip the reader). |
| Screenshots come back empty / `screenshot_error` set | Telegram Web shipped a DOM change. The `SELECTORS` dict at the top of `graea/visual/web.py` is the single place to patch — add a new candidate selector for the affected role, don't rewrite the surrounding logic. |
| Two processes fighting over the same session/DB | Graea is single-writer: run only one of MCP server / HTTP server / CLI command against the same `./data` directory at a time. |

## Staying current

Graea checks GitHub for a newer release on its own — you don't need to poll
manually, but it's cheap to ask (a 24h on-disk cache, 3s network cap, never
raises): run `graea update --check` (or call the `graea_update_check` MCP
tool) at the start of a session. Every non-`mcp`/`serve` CLI command also
prints a one-line `GRAEA_UPDATE: <current> -> <latest> (run: graea update)`
banner to stderr when one is available, `graea status --json` includes a
`version`/`update` field, and every MCP tool result's `notes` carries the
same banner text when a newer release exists — so you'll see it without
asking.

When a human tells you to update, run `graea update` (add `--check` to only
report, never apply). It detects how this install was set up and does the
right thing:

- **Container** users get updates via `podman pull
  ghcr.io/estejosh/graea:latest` — `graea update` does this from inside a
  running container only when podman itself is reachable from there (usually
  it isn't); prefer running the pull on the host, then re-run install.sh /
  restart the MCP server's container.
- **Editable/clone** installs (`pip install -e .` from a git checkout):
  `graea update` runs `git pull --ff-only`, `pip install -e .`, and
  `playwright install chromium` in sequence.
- **Plain pip** installs: `graea update` runs `pip install -U
  git+https://github.com/estejosh/graea`.

Set `GRAEA_CHECK_UPDATES=0` (or `GRAEA_OFFLINE=1`, which also disables it) to
turn the check off entirely — e.g. for an air-gapped environment.

## Reference

- `docs/SETUP.md` — full manual setup (credentials, vision model, tesseract,
  Playwright) if you need more detail than this file.
- `docs/SPEC.md` — the underlying contracts (models, protocol).
- `docs/LLM-GUIDE.md` — how an LLM should drive the MCP tool loop.
- `graea.agent.json` — this document, machine-readable.
