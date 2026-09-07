# Graea

Named for the Graeae, the three sisters of Greek myth who shared a single eye between them. Graea is that eye, passed to whichever LLM is building the bot.

Give any LLM eyes on a Telegram bot it built.

A bot cannot talk to itself, and the Bot API never shows what a human sees:
rendered Markdown, keyboard layout, truncated buttons, edits vs. new
messages, missing captions, silence. Today a human relays that. Graea
replaces the human by driving a real Telegram **user** account against your
bot and reporting back, in one structured result:

- **Structural eye** — Telethon (MTProto) as a test user: sends
  text/commands/files, presses inline & reply buttons, captures every
  incoming message, edit, and delete as structured data.
- **Visual eye** — Playwright driving Telegram Web (`web.telegram.org/k/`),
  logged in as the *same* test user, screenshotting the chat after every
  step.
- **Reader** — a pluggable vision model reads each screenshot and returns a
  plain description plus structured findings (raw markdown, truncated
  buttons, missing captions, stuck spinners, mismatches...). Any
  OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, OpenRouter, OpenAI),
  Anthropic, or an OCR-only fallback (tesseract) — you pick the model.

Every run, step, observation, and assertion is stored in DuckDB, so the LLM
can ask "did I make progress since last run?" and get a real diff instead of
a vibe.

## Quick start

```bash
bash install.sh       # podman build + doctor check; or bash install.sh --no-container for a venv
```

An LLM agent installing this unattended (from the git URL, with minimal human
involvement) should read **[AGENTS.md](AGENTS.md)** instead — it spells out
the exact non-interactive install + login protocol
(`graea.agent.json` is the same thing, machine-readable).

Manual / no-script path:

```bash
pip install -e ".[demo,dev]"

# 1. One-time: log in as your test Telegram user (see docs/SETUP.md for
#    getting api_id/api_hash and a spare account).
graea login          # MTProto (Telethon) login, headless-friendly
graea login-web      # Playwright login to Telegram Web (same account)

# 2. Run the demo bot with a planted bug.
export DEMO_BOT_TOKEN=123456:ABC...          # from BotFather
BUGS=raw_markdown python -m graea.demo.buggy_bot &

# 3. Point Graea at it and run a scenario that catches that bug.
export GRAEA_BOT=@your_demo_bot
graea run graea/demo/scenarios/start_markdown.yaml

# 4. See what changed since the last run.
graea diff
```

`BUGS=` (empty/unset) makes the demo bot behave correctly; a comma list
(`raw_markdown,dead_button,...`) plants specific bugs — see the env table
below. `graea/demo/scenarios/smoke.yaml` walks the whole happy path and
should pass with `BUGS` unset; each other scenario in that directory targets
one specific bug.

## MCP (recommended: how an LLM actually drives this)

Add to your MCP client config (Claude Code, Claude Desktop, or any MCP
client):

```json
{
  "mcpServers": {
    "graea": {
      "command": "graea-mcp"
    }
  }
}
```

Graea reads its settings from env vars / `.env` in the working directory
(see below), so set those before launching the client, or add them under an
`"env"` key in the server config. See `docs/LLM-GUIDE.md` for the tool loop
(`graea_start_run` → act → check assertions/vision/diff → fix → rerun)
written for the LLM that will actually call these tools.

The same operations are also available as a CLI (`graea run`, `graea
step`, `graea diff`, `graea history`, `graea sql`, `graea serve`)
and over HTTP (`graea serve`, a FastAPI wrapper over the same session
API).

## Configuration

Env vars, prefix `GRAEA_` (or a `.env` file in the working directory):

| Var | Default | Meaning |
|---|---|---|
| `GRAEA_API_ID`, `GRAEA_API_HASH` | — | my.telegram.org credentials for the test user |
| `GRAEA_PHONE` | — | test user phone number (login only) |
| `GRAEA_SESSION` | `./data/graea.session` | Telethon session file |
| `GRAEA_SESSION_STRING` | — | Telethon `StringSession`; if set, used instead of the session file (`graea session export` prints one from an existing login) |
| `GRAEA_BOT` | — | default target, `@my_bot` |
| `GRAEA_DB` | `./data/graea.duckdb` | DuckDB memory |
| `GRAEA_SHOTS` | `./data/shots` | screenshot output directory |
| `GRAEA_WEB_PROFILE` | `./data/web-profile` | Playwright persistent context dir |
| `GRAEA_WEB_HEADLESS` | `true` | run Telegram Web headless |
| `GRAEA_REPLY_TIMEOUT_MS` | `8000` | how long to wait for a reply per step |
| `GRAEA_CONNECT_TIMEOUT_S` | `20` | cap on the first MTProto connect (first connect can be slow; `status`/`start` time out cleanly instead of hanging past 30s) |
| `GRAEA_VISION_PROVIDER` | `caller` | `caller` (the driving LLM reads the screenshot itself and reports back with `graea_submit_reading`) \| `openai_compatible` \| `anthropic` \| `ocr` \| `none` |
| `GRAEA_VISION_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint (Ollama default) |
| `GRAEA_VISION_MODEL` | `llama3.2-vision` | vision model name |
| `GRAEA_VISION_API_KEY` | `ollama` | API key for the vision endpoint |
| `GRAEA_OCR` | `auto` | `auto` \| `on` \| `off` — tesseract cross-check |

Demo bot env (separate, not `GRAEA_`-prefixed, see `graea/demo/buggy_bot.py`):

| Var | Meaning |
|---|---|
| `DEMO_BOT_TOKEN` | BotFather token for the demo bot |
| `BUGS` | comma list of planted bugs to enable: `raw_markdown`, `truncated_button`, `edit_as_new`, `missing_caption`, `silent_command`, `dead_button`, `wrong_keyboard_shape` |

See `docs/SETUP.md` for getting credentials and installing the vision/OCR
stack, and `docs/LLM-GUIDE.md` for how an LLM should drive the MCP tools.

## Known limits (v1)

- One driver at a time. The Telethon session file and the DuckDB file are both single-writer, so run either the MCP server, the HTTP server, or CLI commands — not two of them against the same `data/` directory at once.
- Telegram Web K ships DOM changes without notice. Login detection is verified against the 2026 redesign; message-bubble selectors are substring/`data-mid` patterns with a never-fail fallback (bubbles → chat column → viewport). If captures degrade, run `graea web-probe` and hot-fix with `GRAEA_SELECTORS_FILE` (JSON `{role: [css, ...]}`); `SELECTORS` in `graea/visual/web.py` is the single place to patch in code.
- Private chats only; no groups, calls, or payments.

## License

Graea is source-available under the [Elastic License 2.0](LICENSE). You can
use it, modify it, and ship it inside your own products and pipelines; you
can't offer Graea itself to third parties as a hosted or managed service.
Copyright (c) 2026 Joshua D. Hale.
