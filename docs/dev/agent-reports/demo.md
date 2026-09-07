# Agent report: demo

## Files written

- `graea/demo/buggy_bot.py` — python-telegram-bot v22 (async) demo bot.
  Token from `DEMO_BOT_TOKEN`, bugs from `BUGS` (comma list, subset of
  `ALL_BUGS`). Prints active bugs at startup. `python -m graea.demo.buggy_bot`
  runs polling.
- `graea/demo/scenarios/*.yaml` — 8 scenarios: `smoke.yaml` (full happy
  path) plus one per bug (`start_markdown`, `truncated_button`, `order_edit`,
  `missing_caption`, `silent_help`, `dead_button`, `wrong_keyboard_shape`).
  All set `bot: null` and `source_path: graea/demo`.
- `tests/test_scenarios_load.py` — parses every scenario YAML through
  `yaml.safe_load` + `Scenario.model_validate` (standalone loader, per
  AGENT-RULES not touching the real loader). 4 tests.
- `tests/test_demo_bot.py` — unit tests for the pure bug-toggle logic, no
  Telegram connection. 12 tests.
- `README.md` (repo root) — what Graea is, quick start (install, login,
  login-web, run demo bot with `BUGS=raw_markdown`, run a scenario, diff),
  MCP config snippet (`command: graea-mcp`), full env var table.
- `docs/SETUP.md` — my.telegram.org test-user setup, BotFather demo token,
  Ollama (`llama3.2-vision`) / other OpenAI-compatible / Anthropic / OCR-only
  vision setup, tesseract install, Playwright chromium notes (including the
  pre-provisioned `PLAYWRIGHT_BROWSERS_PATH` case), Windows/WSL headless vs.
  headed login notes.
- `docs/LLM-GUIDE.md` — written for the LLM driving the MCP tools: the
  start_run → act → check assertions/vision/diff → fix → rerun loop, how to
  read `progress`, when to call `graea_look` vs `graea_wait`, how to
  write a scenario YAML, 3 example `graea_sql` queries against the
  DuckDB schema, and the "never ask the human what the screen shows" rule.

## Public API (graea/demo/buggy_bot.py)

Pure, Telegram-connection-free functions (unit-testable, used by
`tests/test_demo_bot.py`):

- `active_bugs() -> set[str]` — reads `BUGS` env var.
- `build_start(bugs: set[str]) -> dict` — `{"text", "parse_mode", "reply_markup"}`
  for `/start`. Correctly MarkdownV2-escapes everything except the
  intentional `*bold*` span unless `raw_markdown` is set (then `parse_mode`
  is `None` and the text is sent with literal, unescaped asterisks).
- `build_start_keyboard(bugs) -> InlineKeyboardMarkup` — 1x3
  `["Order","Status","Help"]` normally; `wrong_keyboard_shape` -> 3x1;
  `truncated_button` -> first label padded to exactly 60 chars.
- `build_menu_keyboard() -> ReplyKeyboardMarkup` — `[["A","B"],["C"]]`.
- `build_status(bugs) -> dict` — `{"caption": "Current status" | None}`;
  `missing_caption` -> `None`.
- `generate_status_photo_bytes() -> bytes` — small in-memory PNG via Pillow.
- `build_application(token, bugs) -> Application` — wires handlers + bot_data,
  used by `main()` and reusable for manual smoke testing.

Handlers (`start_command`, `help_command`, `menu_command`,
`callback_handler`, `text_handler`) are thin glue over the pure functions
above; `callback_handler` implements `edit_as_new`, `dead_button`,
`silent_command` (for the inline Help action) directly.

## Test results

```
python -m pytest tests/test_demo_bot.py tests/test_scenarios_load.py -q
16 passed
```

Full-repo `pytest -q` also run: 71 passed, 18 failed — all 18 failures are
in `tests/test_diff.py` and `tests/test_store.py` (owned by other agents),
all raising the same `duckdb.InvalidInputException: Required module 'pytz'
failed to import` — a missing `pytz` dependency in the environment, unrelated
to any file I own. Noted here rather than worked around, per AGENT-RULES
("only create/edit the files assigned to you"); flagging in case it's not
already known — `pytz` isn't in the AGENT-RULES install allowlist.

## Untested / needs real creds

- The bot has not been run against live Telegram (no `DEMO_BOT_TOKEN` /
  bot account provided in this environment) — verified instead via
  `build_application()` with a fake token (handlers register, status photo
  bytes generate, `python-telegram-bot` doesn't validate the token until
  first network call) and via the pure-function unit tests.
- Scenario YAMLs are verified to parse into `models.Scenario` correctly and
  are written to match the assertion kinds in SPEC.md's semantics as I read
  them (e.g. `truncated_button.yaml` uses a `has_inline_button` regex
  `^Order$` plus `vision_no_issues` to catch a padded label), but the actual
  assertion evaluator (`graea/engine/assertions.py`) is owned by another
  agent and wasn't available to cross-check exact matching semantics
  (e.g. whether `has_inline_button.value` is treated as literal-or-regex
  automatically, or needs a distinguishing flag) — worth a quick check once
  that module lands.

## Known gaps

- No end-to-end run through the real MCP/CLI pipeline (owned by other
  agents) — only unit-level verification of the files this agent owns.
