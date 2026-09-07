# memory agent report

Owns: `graea/store.py`, `graea/engine/diff.py`, `graea/engine/fingerprint.py`,
`graea/engine/assertions.py`, and their tests.

## Files written

- `graea/store.py` — DuckDB-backed `Store`.
- `graea/engine/diff.py` — `diff_steps`, `diff_runs`, `run_progress`.
- `graea/engine/fingerprint.py` — `fingerprint`.
- `graea/engine/assertions.py` — `evaluate`, `evaluate_all`, all 16 `AssertionKind`s.
- `tests/test_store.py`, `tests/test_diff.py`, `tests/test_assertions.py`, `tests/test_fingerprint.py`.
- `docs/dev/CONTRACT-NOTES.md` — one note (TIMESTAMPTZ workaround, see below).

All tests pass: `python -m pytest -q` → 128 passed (65 of them mine; the rest
are other agents' existing suites, unaffected).

## Public API the engine author will call

### `graea.store.Store`

```python
Store(path: Path | str)   # ":memory:" allowed; creates schema idempotently on open

# writes (all sync)
create_run(scenario: str, bot_username: str | None, fingerprint: str | None,
           notes: str | None = None) -> RunSummary                      # status="running"
end_run(run_id: str, status: str, notes: str | None = None) -> RunSummary
add_step(run_id: str, idx: int, name: str, action: Action, sent_at: datetime,
         done_at: datetime, timeout_ms: int, timed_out: bool) -> str    # step_id
add_observation(step_id: str, event: ObservationEvent) -> str           # obs_id
add_screenshot(step_id: str, shot: Screenshot) -> str                   # shot_id
add_vision(shot_id: str, reading: VisionReading) -> str                 # reading_id
add_assertion(step_id: str, result: AssertionResult,
              spec: AssertionSpec | None = None) -> str                 # assert_id

# reads
get_run(run_id: str) -> RunSummary                # step_results populated; StepResult.diff is always None
list_runs(scenario: str | None = None, limit: int = 20) -> list[RunSummary]  # step_results == []
previous_run(scenario: str, before_run_id: str) -> RunSummary | None    # most recent *ended* run of scenario before this one
find_step(run_id: str, name: str) -> StepResult | None
assertion_history(scenario: str, assertion_name: str | None = None,
                   limit: int = 50) -> list[dict]
    # rows: {run_id, fingerprint, started_at, step_name, assertion, passed, actual}
sql(query: str, params: list | None = None) -> list[dict]   # read-only; raises ValueError on
    # anything but a single SELECT/WITH statement (comments stripped, one optional trailing ';')
progress_line(run_id: str) -> str
    # "N fixed, M regressed, K still failing, J passing (vs rXXXX)"
    # or "first run: J/K assertions passing" if no previous run of that scenario
```

IDs: `run_id` = `"r" + zero-padded 4-digit incrementing int` per DB (`r0001`,
`r0002`, ...), via a DuckDB `SEQUENCE`. `step_id` = `f"{run_id}s{idx:03d}"`.
Everything else (`obs_id`, `shot_id`, `reading_id`, `assert_id`) is `uuid4().hex`.

Notes for the engine author:
- `RunSummary.progress` is computed on every read that returns a `RunSummary`
  (`create_run` is the exception — nothing to compare yet, so it's `""`).
- `get_run`'s `StepResult.diff` is always `None` and `StepResult.progress` is
  always `""` — diffing is `engine/diff.py`'s job, not the store's.
- `previous_run` only considers runs with `status != 'running'` and a non-null
  `ended_at` (i.e. a run must have been ended to count as "previous").
- `list_runs` DOES compute `progress` for each row despite skipping
  `step_results` (cheap: it's a couple of indexed joins).

### `graea.engine.diff`

```python
diff_steps(current: StepResult, previous: StepResult | None) -> StepDiff
    # assertion-name based; if previous is None, every current assertion
    # lands in new_assertions and nothing else is computed.
    # text_changed: concatenated rendered_text of replies differs
    # keyboard_changed: (kind, shape, button texts) tuples per reply differ
    # vision_issue_delta: len(current.vision.issues) - len(previous.vision.issues)

diff_runs(store: Store, run_a: str, run_b: str) -> DiffReport
    # run_a = baseline, run_b = run being evaluated; steps matched by name.
    # fixed/regressed/still_failing/still_passing/new_assertions entries are
    # "<step name>/<assertion name>" (assertion names can repeat across steps).
    # missing_steps = step names present in run_a but absent from run_b.

run_progress(store: Store, run_id: str) -> str
    # thin delegate to store.progress_line(run_id)
```

### `graea.engine.fingerprint`

```python
fingerprint(source_path: str | Path | None) -> str | None
    # None if source_path is None or doesn't exist.
    # Inside a git repo: short commit hash of HEAD, "-dirty" suffix if
    # `git status --porcelain` is non-empty. Never raises (subprocess
    # failures fall through to the tree-hash path or None).
    # Otherwise: "tree:" + sha256[:12] of sorted (relpath, content) pairs
    # for *.py/*.js/*.ts/*.json/*.yaml/*.toml files under source_path.
```

### `graea.engine.assertions`

```python
evaluate(spec: AssertionSpec, step: StepResult) -> AssertionResult
evaluate_all(specs: list[AssertionSpec], step: StepResult) -> list[AssertionResult]
```

All 16 `AssertionKind`s from `models.py` are implemented (`text_contains`,
`text_equals`, `text_regex`, `reply_count`, `no_reply`, `reply_within_ms`,
`has_inline_button`, `keyboard_shape`, `has_reply_keyboard`, `message_edited`,
`message_deleted`, `markdown_rendered`, `media_type`, `caption_contains`,
`vision_no_issues`, `vision_sees`). Every result's `actual` field is a compact,
human/LLM-readable string of what was really found (not just pass/fail).
`vision_no_issues` / `vision_sees` always FAIL (never silently pass) when
`step.vision` is `None` or `not step.vision.available` — `message` explains why.

Small behavioral notes worth knowing:
- `text_contains`/`text_equals`/`text_regex` search each reply's
  `rendered_text` + `caption` (concatenated), any reply may match.
- `has_inline_button` value starting with `"re:"` is treated as a regex
  against button text; otherwise it's substring-or-equal match honoring
  `spec.case_sensitive`.
- `keyboard_shape` accepts either a list (`[1,1,1]`, compared to
  `Keyboard.shape` exactly) or a string like `"3x1"` (R rows × C cols,
  expanded to `[C]*R` — i.e. it assumes uniform row width).
- `reply_count` defaults to `op=">="` when `spec.op` is unset.
- `markdown_rendered` fails if there are no replies at all (nothing to check).

## Known gaps / untested

- `Store` uses a single `threading.Lock` around every DuckDB call — fine for
  the engine's expected access pattern (one connection, called via executor),
  but not tuned for high concurrency.
- No migration path: schema is `CREATE TABLE IF NOT EXISTS` only — if another
  agent needs a schema change later, they'll need to add `ALTER TABLE`
  statements themselves (idempotent) or bump table names.
- `sql()`'s statement guard is regex/heuristic (strip comments, require a
  single `SELECT`/`WITH` with at most one trailing `;`, no embedded `;`). It
  is deliberately conservative — it does not try to parse the SQL — so it
  will also reject some exotic-but-legal read-only SQL (e.g. a string literal
  containing a semicolon). Good enough for an LLM-facing read tool; flag if
  the interfaces layer needs something less strict.
- Everything is covered by fakes/`:memory:` DuckDB — no real Telegram/vision
  credentials involved anywhere in these four files.

## Contract deviation (logged in docs/dev/CONTRACT-NOTES.md)

SPEC.md's schema sketch says "Timestamps as TIMESTAMPTZ." DuckDB 1.5.5's
Python client raises `_duckdb.InvalidInputException: Required module 'pytz'
failed to import` when reading a `TIMESTAMPTZ` column back (even after `SET
TimeZone='UTC'`), and `pytz` isn't in AGENT-RULES.md's allowed-package list.
Used plain `TIMESTAMP` columns instead (everything written is already a UTC
`datetime`) and reattach `tzinfo=UTC` on read. Values round-trip correctly;
only the on-disk column type differs from the literal spec text.
