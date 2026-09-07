# Contract notes

Notes from build agents about places the spec/contracts needed a local
workaround. Append-only.

## store.py (agent: memory)

- SPEC.md says "Timestamps as TIMESTAMPTZ." DuckDB 1.5.5's Python client
  requires `pytz` to convert a `TIMESTAMPTZ` column back to a Python
  `datetime` on read (`SELECT`), even after `SET TimeZone='UTC'`. `pytz` is
  not in the installed-package allowlist in AGENT-RULES.md and I was told
  not to install packages beyond it. Worked around by using plain
  `TIMESTAMP` columns instead (all datetimes this store writes are already
  UTC-aware, via `models.utcnow()` / `datetime.now(timezone.utc)`), and
  reattaching `tzinfo=UTC` on read (see `_to_utc()` in store.py) before
  handing values back into pydantic models. Round-trip values are correct
  UTC instants either way; only the on-disk column type differs from the
  literal spec text.
