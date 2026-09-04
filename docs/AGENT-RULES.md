# Rules for build agents

- Repo root: /home/claude/graea. Package: `graea`. Python 3.11, async where I/O.
- Read first: docs/SPEC.md, graea/models.py, graea/config.py, graea/client/protocol.py.
  These are the contracts. Do NOT edit them; if something is missing, add a
  note in docs/CONTRACT-NOTES.md (append) describing what you needed and work
  around it locally.
- Only create/edit the files assigned to you. Other agents own the rest.
- Every public function gets a docstring that says what it returns to the LLM.
- Failures are never silent: if an eye can't see (no screenshot, reader down),
  return a model with `error` set, never None-and-move-on.
- Tests: pytest, in tests/test_<yourmodule>.py, must pass with NO Telegram
  credentials and no network. Use fakes. Run `python -m pytest tests/test_<yours>.py -q`.
- Do not git commit. Do not install packages beyond what is already installed
  (telethon, duckdb, playwright, mcp, pydantic, typer, fastapi, uvicorn, pyyaml,
  pillow, pytest, pytest-asyncio, python-telegram-bot, httpx). Chromium for
  playwright is at PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers (do not run playwright install).
  tesseract binary exists; pytesseract is NOT installed — call tesseract via subprocess.
- Keep it practical. No abstractions beyond the spec. Prefer plain functions
  over class hierarchies unless the spec names a class.
- Finish by writing a short summary in docs/agent-reports/<yourname>.md:
  files written, public API, what is untested/needs real creds, known gaps.
