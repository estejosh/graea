# Setup

Everything Graea needs beyond `pip install -e ".[demo,dev]"`.

## 1. A Telegram *test user* account

Graea logs in as a real Telegram user (not a bot) and drives your bot the
way a human would. Use a **spare account** — a second phone number (a cheap
SIM, a Google Voice-style number, or a second device slot) — never your
personal account, since Graea will be sending/reading messages
programmatically under it.

1. Go to <https://my.telegram.org>, log in with the test account's phone
   number.
2. **API development tools** → create an app (any name/platform is fine) →
   note the **App api_id** and **App api_hash**.
3. Set:
   ```bash
   export GRAEA_API_ID=12345678
   export GRAEA_API_HASH=abcdef0123456789abcdef0123456789
   export GRAEA_PHONE=+15551234567   # the test account's number
   ```
4. `graea login` — Telethon (MTProto) login. Prompts for the code
   Telegram sends the test account (and a 2FA password if you've set one).
   Stores a session at `GRAEA_SESSION` (default `./data/graea.session`)
   so you only do this once.
5. `graea login-web` — Playwright login to Telegram Web
   (`web.telegram.org/k/`), same test account. This is a *separate* login
   from step 4 (Telegram Web has its own session) and needs a **headed**
   browser the first time, so it can show the QR code / phone+code flow —
   see the headless/headed note below. Once logged in, the persistent
   context at `GRAEA_WEB_PROFILE` (default `./data/web-profile`) keeps
   you logged in for subsequent headless runs.

## 2. The demo bot's own token

The demo bot (`graea/demo/buggy_bot.py`) is a normal Telegram *bot*,
separate from your test user:

1. Message **@BotFather** on Telegram, `/newbot`, follow the prompts.
2. `export DEMO_BOT_TOKEN=123456789:AA...` with the token it gives you.
3. Add the bot's `@username` as `GRAEA_BOT` so Graea knows who to talk
   to: `export GRAEA_BOT=@your_demo_bot`.
4. From your test user account, start a chat with the bot once manually (or
   let `graea run` do it) so it's in the test user's dialog list.

## 3. A vision model

Pick one:

- **The caller itself (default)** — `GRAEA_VISION_PROVIDER=caller`. No second
  model. Every step hands the driving LLM the screenshot as an image; a
  vision-capable LLM (Claude, GPT, a local VLM) reads it and reports back with
  the `graea_submit_reading` MCP tool (or `POST /reading`). Until it does, the
  step's `vision.error` says "pending" and `vision_*` assertions stay failed,
  so nothing passes on an unread screenshot. Use one of the engines below
  when the driving LLM has no vision, or when you want an independent
  second opinion recorded automatically.

- **Ollama (local, free)**
  ```bash
  # install Ollama: https://ollama.com/download
  ollama pull llama3.2-vision
  ollama serve   # usually already running as a service
  ```
  Set `GRAEA_VISION_PROVIDER=openai_compatible`,
  `GRAEA_VISION_BASE_URL=http://localhost:11434/v1`,
  `GRAEA_VISION_MODEL=llama3.2-vision` (or any vision model you pulled).

- **Any other OpenAI-compatible endpoint** (vLLM, LM Studio, OpenRouter,
  OpenAI itself): set `GRAEA_VISION_BASE_URL`, `GRAEA_VISION_MODEL`,
  `GRAEA_VISION_API_KEY`.

- **Anthropic**: `GRAEA_VISION_PROVIDER=anthropic`,
  `GRAEA_VISION_MODEL=claude-...`, `GRAEA_VISION_API_KEY=sk-ant-...`.

- **No vision model / OCR only**: `GRAEA_VISION_PROVIDER=ocr` (needs
  tesseract, next section) or `GRAEA_VISION_PROVIDER=none` if you only
  want the structural + visual (screenshot, no reading) eyes.

## 4. tesseract (OCR cross-check)

Graea runs OCR alongside whatever vision provider you pick
(`GRAEA_OCR=auto`, on by default when the binary is found) as a
cross-check, and it's the whole reader when `GRAEA_VISION_PROVIDER=ocr`.

```bash
# Debian/Ubuntu
sudo apt-get install -y tesseract-ocr
# macOS
brew install tesseract
```

Graea shells out to the `tesseract` binary directly (not `pytesseract` —
it's intentionally not a dependency). Just make sure `tesseract` is on
`PATH`; no Python-side config needed.

## 5. Playwright / Chromium

```bash
pip install -e ".[demo,dev]"   # already pulls in playwright
playwright install chromium
```

If Chromium is already provisioned at a fixed path in your environment (for
example `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`), skip `playwright
install` — just make sure that env var is set before running anything that
touches the visual eye.

## Container (podman)

Graea ships a `Containerfile` (build/run with **podman**, not docker — see
`AGENTS.md` for why and for the full unattended install protocol an LLM
agent should follow):

```bash
podman build -t graea:latest -f Containerfile .
mkdir -p data
cp .env.example .env   # fill in GRAEA_API_ID / GRAEA_API_HASH / GRAEA_BOT

# one-time logins (interactive, from a terminal):
podman run -it --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest login
podman run -it --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest login-web

# verify:
podman run --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest status --json
podman run --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest doctor

# run the MCP server:
podman run -i --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env graea:latest mcp

# or the HTTP interface:
podman run --rm --userns=keep-id -v "$(pwd)/data:/data:Z" --env-file .env -p 8765:8765 graea:latest serve --host 0.0.0.0
```

`./install.sh` automates the build + `.env` bootstrap + `doctor` check above.
`compose.yaml` (podman-compose) defines `graea-mcp`, `graea-http`, and
`demo-bot` services for a multi-container setup, each with `userns_mode:
keep-id` for the same reason.

The image bakes in tesseract, Playwright's Chromium (at
`PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`), and runs as a non-root `graea`
user (uid 1000). **`--userns=keep-id` is required** for rootless podman to
keep `./data` writable: without it, container uid 1000 maps through
`/etc/subuid` to an unrelated host uid, so a host-owned `./data` is not
writable from inside the container and `doctor`/real commands fail with
`data dir not writable (/data): Permission denied`. `--userns=keep-id` makes
the container user's uid equal your host uid, so the bind mount just works;
`install.sh` also runs a one-time write-test and falls back to `chmod -R
a+rwX ./data` if it fails. `/data` is the container-side mount point for
everything under `./data` on the host (session file, DuckDB, screenshots,
the Telegram Web browser profile) — bind it to persist state across
container runs; without it, every `podman run` starts from a clean,
unauthorized slate.

## Windows / WSL notes

- **WSL is the easier path.** Run everything (test user login, demo bot,
  Graea) inside WSL; Playwright's bundled Chromium works headless there
  out of the box.
- **`graea login-web` needs a real display the first time** (QR code /
  code-entry UI): either run it with `GRAEA_WEB_HEADLESS=false` from a
  machine with a display (or `X11`/`Xvfb` forwarding under WSL), or run it
  once on any machine and copy the resulting `GRAEA_WEB_PROFILE` directory
  to the headless target — Telegram Web sessions live in that browser
  profile, not in a token you can paste.
- **Native Windows Python** works too, but path handling for
  `GRAEA_SESSION` / `GRAEA_WEB_PROFILE` / `GRAEA_SHOTS` is simplest
  if you leave them as relative paths (the defaults) and just run `graea`
  from the project directory.
- After the first `login-web`, set `GRAEA_WEB_HEADLESS=true` (the
  default) for all subsequent runs — CI, scheduled runs, etc.

## Web login notes (from the field)

- Keep `GRAEA_WEB_HEADLESS=true` (the default) and let `graea login-web`
  screenshot the QR for you. Headed mode against an Xwayland/mutter DISPLAY
  has hung on the first navigation; headless loads in ~1.5s.
- Telegram Web K lands on the phone-number form; `login-web` clicks through
  to the QR page itself and waits for the QR canvas to actually render.
  If it prints a blank-canvas warning, Telegram did not issue a token (rate
  limit or refusal): wait a few minutes and re-run.
- Inside the container, `web.telegram.org` may resolve to an IPv6 address
  with no route. Chromium falls back to IPv4 on its own; if your resolver is
  IPv6-only, run podman with `--network=slirp4netns:enable_ipv6=false` or
  add `--disable-ipv6` via a Chromium flag file, and prefer IPv4 DNS.
- Two-step verification: set `GRAEA_2FA_PASSWORD` in `.env`. `graea login`
  (MTProto) and `graea login-web` (Telegram Web) both use it; the web flow
  clicks the visible `.input-field-password` and types the password because
  the real inputs are hidden.
- The web eye needs ~10-20s after launch to settle; `status --json` waits up
  to `GRAEA_WEB_LOGIN_SETTLE_S` (20) for a positive logged-in signal.
