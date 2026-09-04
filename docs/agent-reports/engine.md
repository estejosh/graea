# engine agent report

Owns: `graea/engine/runner.py` (`TestSession`), `graea/engine/scenario.py`,
`tests/test_runner.py`.

## Files written

- `graea/engine/runner.py` — `TestSession`.
- `graea/engine/scenario.py` — `load_scenario`, `scenario_from_dict`, `scenarios_dir`.
- `tests/test_runner.py` — 9 tests, all passing.

`python -m pytest -q` → **137 passed** (128 pre-existing + 9 new), nothing
else touched or broken.

## Signature check

`TestSession.__init__` and every public method match the signature given in
the task exactly — no deviation. `load_scenario(path_or_name)` accepts either
an existing file path or a bare name (with or without `.yaml`), resolved
against `scenarios_dir()` (`graea/demo/scenarios/<name>.yaml`); raises
`FileNotFoundError` if neither resolves.

## Contract notes for the MCP/CLI/HTTP authors

- **Injected vs. built dependencies**: `transport`/`web`/`reader`/`store`
  passed to `__init__` are used as-is; `None` ones are built from `settings`
  in `start()`. Regardless of injected-or-built, `start()` always calls
  `transport.connect()`, `web.start()` + `web.is_logged_in()`, and
  `probe(reader)` — so a test double must implement `start()`/`stop()`/
  `is_logged_in()` even as no-ops (see `StubWeb` in `tests/test_runner.py`).
  Only when `web` was **not** injected (built here) does a `start()`/
  `is_logged_in()` failure fall back to replacing it with `NullWeb()`; an
  explicitly-injected `web` (including an explicitly-passed `NullWeb()`) is
  never swapped out — its failure just sets the health/`notes` reason.
- **Screenshot/vision failure is never special-cased by type** — `step()`
  just try/excepts `web.screenshot(...)`; `NullWeb`'s
  `RuntimeError("visual eye disabled")` naturally becomes
  `StepResult.screenshot_error = "visual eye disabled"` and a matching note
  (`"visual eye disabled: visual eye disabled"` — the note text always
  starts with `"visual eye disabled: "` whenever the caught exception's
  message contains the word "disabled", otherwise `"screenshot failed: ..."`).
  `vision` stays `None` whenever there's no screenshot (never a fake
  reading) — `vision_no_issues`/`vision_sees` then FAIL via
  `assertions.py`'s own "vision unavailable" path, with the reason in
  `AssertionResult.message`.
- **`self.last_replies`** is not "this step's replies" but an
  accumulating, edit-aware cache of every bot `ObservedMessage` seen so far
  in the *current run* (reset in `start_run`), used by `press_button`'s
  target resolution: `target_message_id` if given, else the most recent
  message in `self.last_replies` with an inline keyboard, else the same
  search over a fresh `transport.last_bot_messages(10)` call, else the most
  recent reply-keyboard message from either source. If the resolved
  target's keyboard is `kind="reply"`, `press_reply_button(action.button_text)`
  is used instead of `press_inline`; `action.button_text` is then required
  (a `ValueError` is raised — caught and turned into an `"action failed: ..."`
  note — if it's missing).
- **Callback answers become an event**: on `press_button` against an inline
  keyboard, a successful `press_inline` return value is appended to `events`
  as `ObservationEvent(kind="callback_answer", detail=...)` (after the normal
  `wait_for_events` call) — so it shows up in `StepResult.events` and counts
  toward "not timed out" even if the bot sent no message. If `press_inline`
  returns `None` (bot never answered — the `dead_button` case), a
  `"callback not answered by bot"` note is added and no synthetic event is
  added, so a step with no other reply is legitimately `timed_out=True`.
- **`timed_out`** is `False` whenever any event occurred (including a bare
  `callback_answer`); for `kind in (look, wait)` with an *empty* `expect`
  list it is always `False` even with zero events (per spec — "observe
  only" isn't a failure); otherwise (including `wait`/`look` *with*
  expectations, and every other action kind) zero events means
  `timed_out=True` and a `"no reply within {timeout_ms}ms"` note.
- **Step names**: default is `f"{idx:02d}-{action.kind.value}-{short}"`
  where `short` is `action.text or action.button_text or action.file_path`,
  trimmed to 24 chars (falls back to `"step"` if all are empty/None).
- **Screenshot path**: `Path(settings.shots) / run_id / f"{step_id}.png"`,
  exactly as specced; `step_id` is deterministic (`Store`'s
  `f"{run_id}s{idx:03d}"`) so this is stable even though the path is built
  before knowing whether the screenshot will actually succeed.
- **Diff/progress per step**: `store.previous_run(scenario_name, run_id)` →
  `store.find_step(prev_run.run_id, step_name)` (matched by *name*, so scenario
  step names must be stable across runs to get useful diffs) →
  `diff_steps(current, prev_step_or_None)`. `StepResult.progress` is set to
  `StepDiff.summary` (e.g. `"step 'help': 1 fixed, 0 regressed, ... "`), which
  is a *per-step* line, distinct from `Store.progress_line`'s *per-run* line
  (`"N fixed, M regressed, ... (vs rXXXX)"`) used inside `RunSummary.progress`
  by the store itself. `RunSummary.progress` (from `store.end_run`) and each
  `StepResult.progress` are both populated, at two different granularities —
  interfaces should show whichever fits.
- **`end_run` resets `self.run_id` to `None`** (so the next `step()` call
  auto-starts a fresh "adhoc" run) but leaves `self.scenario_name` alone, so
  `session.diff()` with no arguments still resolves to the scenario of the
  run that was just ended.
- **`diff()` resolution** when `run_a`/`run_b` aren't both given: resolves
  `scenario` (arg, else `self.scenario_name`; raises `ValueError` if neither
  is available), then `run_b` defaults to the most recent *ended* run of
  that scenario (`store.list_runs(scenario, limit=50)` filtered to
  `status != "running"`, which is already newest-first) and `run_a` to
  `store.previous_run(scenario, run_b)`. Raises `ValueError` with a specific
  message if there aren't at least two ended runs to compare.
- **`Health.web_error`/vision fields**: `health()` re-probes live each call
  (`transport.is_connected()`/`.me()`, `web.is_logged_in()`, `probe(reader)`)
  rather than caching `start()`'s result, so it reflects the current state
  (e.g. after a mid-session disconnect) — all three are best-effort and never
  raise out of `health()`.

## Known gaps / untested

- No dedicated test exercises `TelethonTransport`/`TelegramWeb`/a real
  vision provider through `TestSession` — per `AGENT-RULES.md`, all engine
  tests use `FakeTransport` + a stub web/reader + `Store(":memory:")`. The
  `start()` fallback-to-`NullWeb` path (real `TelegramWeb()` failing to
  start/log in) is exercised only indirectly, by injecting `NullWeb()`
  directly in the "screenshot_error" test — behaviorally identical from
  `step()`'s point of view, but the actual `TelegramWeb.start()` exception
  path itself is unverified here (same caveat the visual agent's report
  already carries).
- `send_file` and `look`/`wait` action kinds are exercised by other agents'
  demo-scenario coverage (`test_demo_bot.py`, `test_scenarios_load.py`) but
  have no dedicated `test_runner.py` case beyond what's implied by the
  action-kind branch structure; behavior for those two mirrors
  `send_text`/`send_command` closely enough that this was judged low-risk
  given time, but worth a follow-up test if `send_file`/`wait`/`look`
  develop their own bugs.
- `assert_last`'s effect on `self._run_ok` (failing an `assert_last` call
  fails the eventual `end_run(status=None)` inference) is implemented but
  not directly asserted in a test — only that the returned results and
  `last_step.assertions` are correct.

No deviations from the task's `TestSession`/`load_scenario` signatures.
