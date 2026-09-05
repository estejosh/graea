# LLM guide to Graea

## You are probably the reader

With the default `GRAEA_VISION_PROVIDER=caller`, no separate vision model
runs. Every step result includes the screenshot as an image block. Look at
it, then call `graea_submit_reading(description, issues)` with what you saw
(`issues=[]` if it looks right). That stores your reading, re-evaluates the
step's `vision_*` assertions, and returns the refreshed StepResult. Until you
submit, `vision.error` reads "pending" and vision assertions fail on purpose:
an unread screenshot never counts as clean. If you cannot see images, ask the
operator to configure an engine (`openai_compatible`, `anthropic`, `ocr`) and
read `vision.description` / `vision.issues` instead.

You are an LLM driving your own Telegram bot through the `graea_*` MCP
tools (see `docs/SPEC.md` for the full tool surface and schemas). This is
written for you, not for the human. The core rule:

> **Never ask the human what the screen shows. Call `graea_look`.**

They can't see any better than you can — the whole point of Graea is that
you don't need them to. If something is ambiguous from the structural result
alone (does the button *look* wrong, is the layout broken, is there a
spinner stuck), call `graea_look` and read the vision reading and the
returned screenshot yourself.

## The loop

1. **`graea_start_run(scenario=..., bot=..., source_path=...)`** — pass
   `source_path` pointing at your bot's source tree (e.g. `graea/demo`).
   Graea fingerprints it (git commit or tree hash) so later diffs can be
   pinned to a code change. Use a real scenario name matching a YAML file
   under `<source_path>/scenarios/` when you have one; `"adhoc"` for
   free-form poking.
2. **Act**: `graea_send`, `graea_command`, `graea_press_button`,
   `graea_send_file`, or `graea_wait` (no action, just observe —
   useful when you expect an async reply). Each returns a `StepResult`.
3. **Read the `StepResult`, in this order:**
   - `assertions` — did the scenario's own expectations pass? This is the
     fast, cheap signal.
   - `vision.issues` — even when assertions pass, check this. A `raw_markdown`,
     `truncated_button`, `missing_caption`, `layout`, or `mismatch` finding
     here is a real bug your assertions might not have thought to check for.
     `vision.error` set (not `None`) means the reader couldn't run — that's
     a tooling problem, not "no issues", so don't treat it as a pass.
   - `diff` / `progress` — `progress` is one line comparing this run's step
     to the same-named step in the *previous* run of the same scenario
     ("2 fixed, 1 regressed, 4 still failing since run 3"). Use it to know
     whether your last code change actually helped before you touch
     anything else.
4. **Fix the code**, restart your bot process, and **rerun the same
   scenario** (`graea_run_scenario` or replay the steps). Comparing to
   the *same* scenario name is what makes the diff/progress line meaningful
   — a new scenario name starts a fresh history.
5. **`graea_diff(scenario=...)`** for a full before/after report across
   an entire run (fixed / regressed / still-failing / still-passing /
   new-assertions), not just one step. Call this after a batch of fixes to
   confirm nothing you fixed broke something else.
6. **`graea_end_run(status=...)`** when the scenario is done — `"passed"`
   if everything you care about passed, `"failed"` otherwise, with `notes`
   summarizing anything odd (e.g. a flaky timeout you'd want a human to
   know about).

## `graea_look` vs `graea_wait`

- **`graea_wait(timeout_ms)`** — no action; observes structurally for up
  to `timeout_ms` and returns whatever arrived (new messages, edits,
  deletes). Use this when you *sent* something and are waiting for an async
  reply that might take a moment, or want to confirm nothing came back
  (silence is itself a bug — `silent_command`-style).
- **`graea_look(last_n=5, annotate=False)`** — takes a fresh screenshot of
  the last N messages and runs the vision reader, *without* sending any
  action. Use this whenever you need to know what a human would actually
  see and the structural data alone doesn't answer it: is the markdown
  rendering as bold or as literal asterisks, is the keyboard one row or
  three, is there a stuck loading spinner on a button, does a photo look
  broken. It's also how you resolve any assertion failure that mentions
  layout, rendering, or "mismatch" — don't guess, look.

Both return a `StepResult` (with `assertions` empty for `graea_wait`/
`graea_look` since no `expect` list applies) and the same screenshot +
vision-reading shape as an acting step, so the rest of the loop treats them
identically.

## Writing a scenario YAML for your own bot

Match `graea.models.Scenario` / `ScenarioStep` exactly — see
`graea/demo/scenarios/*.yaml` for worked examples covering every action
and assertion kind. Minimal shape:

```yaml
name: my_scenario
description: what this checks and why
bot: null              # null = use the configured GRAEA_BOT
source_path: path/to/your/bot
steps:
  - name: start
    action:
      kind: send_command   # send_text | send_command | press_button | send_file | wait | look
      text: /start
    expect:
      - kind: text_contains
        value: "Welcome"
      - kind: keyboard_shape
        value: [3]          # one row of 3 buttons
      - kind: vision_no_issues
  - name: press_order
    action:
      kind: press_button
      button_text: Order     # or button_index / button_row+button_col
    expect:
      - kind: message_edited
      - kind: vision_sees
        value: "Order placed"
```

One scenario per behavior you care about beats one giant scenario — it keeps
the per-step diff/progress history meaningful (`graea run` re-runs by
scenario name). Always include a `vision_no_issues` (or targeted
`vision_sees`) assertion on any step whose whole point is "does this look
right to a human" — text/keyboard-shape assertions alone miss rendering bugs
by construction.

## `graea_sql` for trend questions

`graea_sql` runs a read-only `SELECT` against the DuckDB schema in
`docs/SPEC.md` (`runs`, `steps`, `observations`, `screenshots`,
`vision_readings`, `assertions`). Use it for questions the per-run tools
don't answer directly.

**Has this specific assertion ever passed, across all runs of a scenario?**
```sql
SELECT r.run_id, r.started_at, a.passed
FROM assertions a
JOIN steps s ON s.step_id = a.step_id
JOIN runs r ON r.run_id = s.run_id
WHERE r.scenario = 'start_markdown' AND a.name = 'markdown_rendered'
ORDER BY r.started_at;
```

**Which fingerprints (code versions) introduced regressions?**
```sql
SELECT r.fingerprint, r.started_at, r.assertions_passed, r.assertions_total
FROM runs r
WHERE r.scenario = 'smoke'
ORDER BY r.started_at DESC
LIMIT 20;
```
(Compare `assertions_passed`/`assertions_total` run-over-run to spot the
fingerprint where the ratio dropped — that's the commit that broke it.)

**How often does the vision reader flag issues on a given step, and what
kind?**
```sql
SELECT json_extract_string(f.value, '$.kind') AS issue_kind, count(*) AS n
FROM vision_readings v
JOIN screenshots sh ON sh.shot_id = v.shot_id
JOIN steps s ON s.step_id = sh.step_id
CROSS JOIN LATERAL json_each(v.findings_json) AS f
WHERE s.name = 'start'
GROUP BY 1
ORDER BY n DESC;
```

Prefer `graea_diff`/`graea_history` for the common "did I just fix
this" question — reach for `graea_sql` when the question spans many runs
or needs aggregation those tools don't do.
