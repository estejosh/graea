# Graea — architecture spec

Give any LLM eyes on a Telegram bot it built. The LLM drives a real Telegram
**user** account against the bot, gets back structured data *and* real
screenshots *and* a machine reading of those screenshots, and every step is
stored in DuckDB so the LLM can ask "did I make progress since last run?".

## Problem

A bot cannot talk to itself. The Bot API never shows what a human sees:
rendered Markdown, keyboard layout, truncated buttons, edits vs new messages,
missing captions, silence. Today a human relays that. Graea replaces the
human.

## Three eyes, always together

1. **Structural eye** — Telethon (MTProto) logged in as a test user. Sends
   text/commands/files, presses inline & reply buttons, captures every
   incoming message, edit and delete as structured data.
2. **Visual eye** — Playwright driving Telegram Web (`web.telegram.org/k/`)
   logged in as the *same* test user, screenshotting the chat after every step.
3. **Reader** — a pluggable vision model reads each screenshot and returns a
   plain description + structured findings. Provider-agnostic: any
   OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, OpenRouter, OpenAI),
   Anthropic, or OCR-only fallback (tesseract). The user picks the LLM.

Nothing in a step result should force the LLM to ask a human "what do you see".

## Memory: DuckDB

Every run/step/observation/assertion is stored. Each step result carries a
diff against the same step in the previous run of the same scenario, and a
progress line ("2 fixed, 1 regressed, 4 still failing since run 3"). Runs are
fingerprinted with the bot's git commit (or source-tree hash) so regressions
can be pinned to a change.

## Package layout (Python 3.11, package `graea`)

```
graea/
  config.py            Settings (pydantic-settings, env prefix GRAEA_)
  models.py            Pydantic contracts — THE shared interface
  store.py             DuckDB schema + queries + diff/trend helpers
  client/
    protocol.py        TransportProtocol (what the engine needs from a client)
    mtproto.py         TelethonTransport implements TransportProtocol
    serialize.py       telethon Message -> ObservedMessage
    fake.py            FakeTransport for tests (scripted replies)
  visual/
    web.py             TelegramWeb: Playwright persistent context, open chat,
                       screenshot chat / last N messages, optional visual click
    vision.py          VisionReader protocol + OpenAICompatibleVision,
                       AnthropicVision, OcrVision, NullVision, factory
    prompts.py         vision prompt (description + JSON findings)
  engine/
    runner.py          TestSession: one step = act -> wait -> observe ->
                       screenshot -> read -> store -> diff -> StepResult
    assertions.py      assertion evaluators
    scenario.py        YAML scenario loader + runner
    diff.py            run-vs-previous-run diff -> DiffReport
    fingerprint.py     git commit / tree hash of bot source
  interfaces/
    mcp_server.py      FastMCP server (stdio) — primary LLM interface
    cli.py             typer CLI (login, run, step, diff, history, sql, serve)
    http.py            FastAPI wrapper over the same session API
  demo/
    buggy_bot.py       python-telegram-bot demo with planted bugs
    scenarios/*.yaml   scenarios that catch those bugs
tests/                 pytest; no Telegram creds needed (FakeTransport,
                       NullVision, real DuckDB in tmp)
docs/                  SPEC.md, SETUP.md, USAGE.md, LLM-GUIDE.md
```

## Contracts (see `graea/models.py` — authoritative)

Key models: `Button`, `Keyboard`, `MediaInfo`, `ObservedMessage`,
`ObservationEvent` (new/edit/delete), `Screenshot`, `VisionFinding`,
`VisionReading`, `Action`, `AssertionSpec`, `AssertionResult`, `StepDiff`,
`StepResult`, `RunSummary`, `DiffReport`, `Scenario`, `ScenarioStep`.

`StepResult` is what every interface returns to the LLM. It must always
include: what was done, what came back (structured), screenshot path + sha256,
vision reading (or an explicit reason it is absent), assertion results, diff vs
previous run, and `progress` (one human line).

## DuckDB schema (see `graea/store.py`)

```
runs(run_id, scenario, bot_username, fingerprint, started_at, ended_at, status, notes)
steps(step_id, run_id, idx, name, action_json, sent_at, done_at, timeout_ms, timed_out)
observations(obs_id, step_id, kind, message_id, chat_id, occurred_at, payload_json,
             rendered_text, has_keyboard, keyboard_json, media_json)
screenshots(shot_id, step_id, path, sha256, width, height, taken_at)
vision_readings(reading_id, shot_id, provider, model, description, findings_json,
                ocr_text, latency_ms, error)
assertions(assert_id, step_id, name, kind, spec_json, passed, actual, message)
```

Store exposes: `create_run`, `end_run`, `add_step`, `add_observation`,
`add_screenshot`, `add_vision`, `add_assertion`, `previous_run(scenario)`,
`step_results(run_id)`, `diff_runs(a,b)`, `assertion_history(scenario, name)`,
`sql(query)` (read-only), `progress_line(run_id)`.

## MCP tool surface (`graea/interfaces/mcp_server.py`)

```
graea_start_run(scenario: str = "adhoc", bot: str|None, source_path: str|None) -> RunSummary
graea_send(text) -> StepResult
graea_command(command) -> StepResult          # "/start"
graea_press_button(text|None, index|None, row|None, col|None) -> StepResult
graea_send_file(path, caption|None) -> StepResult
graea_wait(timeout_ms) -> StepResult          # observe only, no action
graea_look(last_n=5, annotate=False) -> StepResult   # screenshot + vision, no action
graea_assert(assertions: list[AssertionSpec]) -> list[AssertionResult]  # against last step
graea_run_scenario(path_or_name) -> RunSummary (with all StepResults)
graea_diff(scenario|None, run_a|None, run_b|None) -> DiffReport
graea_history(scenario, assertion|None, limit) -> rows
graea_sql(query) -> rows                      # read-only SELECT
graea_end_run(status|None, notes|None) -> RunSummary
graea_status() -> health: mtproto connected, web logged in, vision provider, db path
```

Every tool that produces a screenshot returns the JSON text **and** an MCP
image content block so vision-capable LLMs see it too; non-vision LLMs rely
on the reading. Same operations exposed via CLI and HTTP.

## Assertion kinds

`text_contains`, `text_equals`, `text_regex`, `reply_count` (==, >=),
`no_reply`, `reply_within_ms`, `has_inline_button` (by text/regex),
`keyboard_shape` (rows x cols), `has_reply_keyboard`, `message_edited`,
`message_deleted`, `markdown_rendered` (no stray `*`/`_`/backticks in
rendered text; entities present), `media_type`, `caption_contains`,
`vision_no_issues` (reader found no findings above severity),
`vision_sees` (reader description contains).

## Vision reading contract

Prompt asks the model for: (1) a plain description of the visible chat,
newest message last; (2) JSON: `messages_seen[]` (sender, text_as_rendered,
buttons[], media), `issues[]` (severity, kind, detail) where kinds include
`raw_markdown`, `truncated_button`, `missing_caption`, `empty_message`,
`broken_media`, `layout`, `other`. The reader must also cross-check against
the structural expectation passed in (expected text/buttons) and flag
mismatches as `mismatch`. OCR text (if tesseract present) is stored alongside
regardless of provider.

## Config (env, prefix `GRAEA_`, also `.env`)

```
GRAEA_API_ID, GRAEA_API_HASH          my.telegram.org creds (test user)
GRAEA_PHONE                              test user phone (login only)
GRAEA_SESSION=./data/graea.session
GRAEA_BOT=@my_bot                        default target
GRAEA_DB=./data/graea.duckdb
GRAEA_SHOTS=./data/shots
GRAEA_WEB_PROFILE=./data/web-profile     Playwright persistent context
GRAEA_WEB_HEADLESS=true
GRAEA_REPLY_TIMEOUT_MS=8000
GRAEA_VISION_PROVIDER=caller             | openai_compatible | anthropic | ocr | none
GRAEA_VISION_BASE_URL=http://localhost:11434/v1   (Ollama default)
GRAEA_VISION_MODEL=llama3.2-vision
GRAEA_VISION_API_KEY=ollama
GRAEA_OCR=auto                           auto|on|off
```

## Demo bot (planted bugs, each toggled by env `BUGS=a,b,c`)

`raw_markdown` (sends `*bold*` with wrong parse_mode), `truncated_button`
(60-char button label), `edit_as_new` (callback posts new message instead of
editing), `missing_caption` (photo without caption), `silent_command`
(`/help` never answers), `dead_button` (callback never answered → spinner),
`wrong_keyboard_shape` (3x1 instead of 1x3). Scenarios in
`demo/scenarios/` assert each. With `BUGS=` empty everything passes.

## Non-goals (v1)

Group chats, voice/video calls, payments, multiple simultaneous test users.
