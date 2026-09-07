# Changelog

All notable changes to Graea are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

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
