# Graea preview video: claims ledger

Every product claim in `SCRIPT.md` and on screen traces to a row here.
Evidence is `file:line` from the repo at v0.1.6 (local checkout @ `1a5d963`), a test name, or output from a command that was run.
Remote `main` is now `ea4b8b5`: four commits after `1a5d963` (UFL-2.1 and UFL-2.2 bumps, README citation) that, by their commit messages, touch only license text and the README. Code and test claims below were checked at `1a5d963`; license claims were re-checked against `main` @ `ea4b8b5`.
Statuses: **verified** (read in code and/or exercised), **partial** (mechanism verified, live run not done), **unverified**, **do not say**.

How it was checked: code read on `X:\Graea`; `pip install -e ".[demo,dev]"` into a throwaway venv (Python 3.14); full `pytest` run:
**224 passed, 1 skipped, 9 failed**. All 9 failures are in `tests/test_web.py` with `Executable doesn't exist ... chrome-headless-shell.exe`
(Playwright's Chromium is not installed on that machine; CI installs it, `.github/workflows/ci.yml`). Not a code defect, but those 9 were not green in this run.
No live Telegram run was done (needs a test user session and a bot token).

## Product claims used in the video

| # | Claim (plain words) | Evidence | Status |
|---|---|---|---|
| 1 | Graea logs in as a test **user** (not a bot token) and messages your bot | README:7-15; `graea/client/mtproto.py` `connect` :62, `login_interactive` :96; `pyproject.toml`:13 `telethon>=1.36,<2` | verified |
| 2 | It taps inline buttons "like a person" | `mtproto.py` `press_inline` :200 (reply-keyboard buttons too, `press_reply_button` :277); MCP `graea_press_button` `mcp_server.py`:204 | verified |
| 3 | It can also send text, commands, files with captions, and records replies, edits, deletes | `mtproto.py` `send_text` :173, `send_file` :179, handlers `_on_new` :151 / `_on_edit` :156 / `_on_delete` :161 | verified |
| 4 | It screenshots the chat as rendered, using a browser logged in as the same test user | README:16-18; `graea/visual/web.py` (docstring :1, `open_chat` :421, `screenshot` :480) | verified in code (not exercised here: Chromium missing on the test machine) |
| 5 | A vision model reads the screenshot and **you choose which one** | `graea/visual/vision.py`: `OpenAICompatibleVision` :170, `AnthropicVision` :239, `OcrVision` :310 (tesseract), `CallerVision` :338 (default, your AI reads it, reports via `graea_submit_reading` `mcp_server.py`:285), `NullVision` :359; `make_reader` :374; README:19-23, :111 | verified |
| 6 | Every step is stored in DuckDB | `graea/store.py` tables `runs` :52, `steps` :64, `observations` :77, `screenshots` :93, `vision_readings` :105, `assertions` :119; read-only SQL guard :133; MCP `graea_sql` :370 | verified |
| 7 | The AI can ask for history and a **diff** between runs ("did I make progress") | `graea_diff` `mcp_server.py`:341 (`fixed` / `regressed` / `still_failing` / `still_passing`), `graea_history` :356, `progress` on `graea_start_run`/`graea_end_run`; tests `test_runner.py::test_run_scenario_twice_diffs_fixed_and_regressed`, `tests/test_diff.py` (pass) | verified (against the fake transport, not live Telegram) |
| 8 | Your AI plugs in over MCP | `graea-mcp` entry point `pyproject.toml`:40; README:68-87 | verified |
| 9 | **The MCP server exposes 16 tools** (start_run, end_run, send, command, press_button, send_file, wait, look, assert, submit_reading, run_scenario, diff, history, sql, status, update_check) | `mcp_server.py` `@mcp.tool()` at :126, 146, 171, 189, 204, 224, 238, 252, 268, 285, 317, 341, 356, 370, 388, 404 | verified. The recording says "15 tools": **wrong, cut from the audio** |
| 10 | Each step returns a structured result (assertions with pass/fail and actual value, vision description/issues, `timed_out`) plus the screenshot, so the AI is told what failed and at which step | `mcp_server.py` docstrings :171-186, :204-222, :268-283; `_step_content` | verified (the on-screen JSON is illustrative in shape, field names are real) |
| 11 | It can detect: raw markdown, truncated buttons, missing captions, a button whose callback is never answered | Assertion kinds `markdown_rendered`, `has_inline_button`, `caption_contains`, `media_type`, `keyboard_shape`, `message_edited`, `vision_no_issues` in `graea/engine/assertions.py`; scenarios `start_markdown`, `truncated_button`, `missing_caption`, `dead_button` in `graea/demo/scenarios/`; vision finding kinds in `submit_reading` docstring `mcp_server.py`:285-306 | **partial**: assertion logic and scenarios exist and the unit tests pass; **no live end-to-end run against a real bot yet** (that is what the planned public demo bot would prove) |
| 12 | The demo bot has planted, togglable bugs (7: `raw_markdown`, `truncated_button`, `edit_as_new`, `missing_caption`, `silent_command`, `dead_button`, `wrong_keyboard_shape`) | `graea/demo/buggy_bot.py`:53-61 (`ALL_BUGS`), `BUGS` env :77-85; `tests/test_demo_bot.py` (13 tests on the planted payloads, pass) | verified |
| 13 | The dead-button bug = the bot never answers the button callback (spinner stays) | `buggy_bot.py`:192-195 (`_maybe_answer` returns before `query.answer()` when `dead_button` is on); `scenarios/dead_button.yaml` asserts `message_edited` + `vision_no_issues` | verified |
| 14 | The fix shown on screen (remove the `dead_button` early return so `query.answer()` runs) | same lines `buggy_bot.py`:192-195 | verified (it is the demo bot's own planted bug) |
| 15 | License: Usufruct License (UFL), currently **v2.2**, Operational Scope No-Third-Party-Hosting; source-available, not OSI open source | `LICENSE` on `main`:1-5 ("Version 2.2"), Section 1A, Section 6 ("a source-available license, not an OSI-approved open source license"); commits 836e5e9 (2.1), 79983c4 (2.2), ea4b8b5 (README cites 2.2). The end card shows "Usufruct License (UFL)" with **no version number**, so it stays true across bumps | verified |
| 16 | Source is at github.com/estejosh/graea | `pyproject.toml`:34-36; `git remote -v` | verified |
| 17 | Install works today (`pip install -e ".[demo,dev]"`) | ran it: installed graea 0.1.6, Telethon 1.45.0, duckdb 1.5.6, pytest 9.1.1 (Python 3.14) | verified for the pip path. `install.sh` (podman) not run |
| 18 | No human has to relay screenshots in the loop (the reader is a model or the driving AI) | provider design above; `CALLER_PENDING` flow `vision.py`:338-356 | verified. Note: a **one-time human login** is still needed (`graea login`, `graea login-web`, README:45-48) |

## Illustrative on screen (tagged "illustrative run")

| What | Why it is not a claim |
|---|---|
| `@demo_pizza_bot`, "Demo Pizza Bot", "Welcome to Demo Pizza", an "order summary" | fictional bot and copy (the repo's demo bot says "Order placed"); the chat UI is a synthetic generic messenger |
| run 1: 3 failures, run 2: 1 failure, run 3: all pass; "fixed 3, regressed 0" | storyboard numbers, not a benchmark. Diff key names are real (#7). The three failing assertion names are real kinds (#11) |
| Order of the loop (act, capture, read, log, fix, re-check) | a narrative summary of `graea_start_run` -> act -> check -> fix -> rerun (README:85-87) |

## Do not say

| Do not say | Why |
|---|---|
| "15 tools" | there are 16 (#9). Recorded line cut. Optional pickup: "sixteen tools." |
| "open source" | UFL is source-available, not OSI open source (`LICENSE` Section 6, README) |
| "Elastic License 2.0" / "ELv2" | stale. `pyproject.toml`:11 on `main` still says `Elastic-2.0` but `LICENSE` and README are UFL. **Fix in a separate change** (config files are out of scope for this PR) |
| "UFL v2.0" or any version number on screen | the license has already moved 2.0 to 2.1 to 2.2. The first render said v2.0 and was re-rendered |
| "on PyPI" / `pip install graea` | no PyPI publish step anywhere. `release.yml` builds a container image to GHCR and a GitHub Release on `v*` tags (tags v0.1.0 to v0.1.6 exist; whether each run succeeded was not checked) |
| "all tests pass" / any test count | 9 web tests could not run on the test machine (Chromium missing) |
| "catches every bug" / "works on any bot" | live detection is unproven (#11); scenarios target the demo bot |
| groups, channels, calls, payments | README:131 "private chats only" |
| specific vision hosts (Ollama, vLLM, LM Studio, OpenRouter, OpenAI) as supported | code supports the *OpenAI-compatible protocol* (#5); named hosts are README examples with no test |
| "fully autonomous", "replaces QA" | brief rule; also the login is manual (#18) |
| "hosted", "SaaS", "as a service" | the license forbids offering Graea to third parties as a hosted or managed service (`LICENSE` Section 1A) |
| "stable", "never breaks" about the visual eye | README:130: Telegram Web K ships DOM changes without notice |
| Telegram as partner/endorser; real chats, numbers, usernames, tokens | brief rule. Spoken "Telegram" is descriptive only; no Telegram logo or branding on screen |

## Recorded-line audit (what Josh said vs the script)

Captions are built from the words actually spoken. Spoken but different from the draft script:

- "every step goes into **duckdb log**" (draft: "a duckdb log"), kept as spoken
- "run three, all **passed**" (draft: "all pass"), kept as spoken (Cut B says "all pass")
- Cut B adds "and **where**" after "exactly what broke", kept as spoken
- End card adds "**usufruct licensing. ufl all the way.**" (not in the draft). The license name is verified (#15); "UFL all the way" is a slogan, not a claim
- Cut B end line uses the third end-card take (the one with "ufl all the way")
- Audio edits: dropped the false start "graea gives you...", the stumble "markdown, truncated bucket", and "15 tools." (wrong count); dropped the two earlier end-card takes in Cut B. Nothing else was altered
