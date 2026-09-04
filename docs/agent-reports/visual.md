# visual agent report

Files written (only files owned by this agent):

- `graea/visual/web.py`
- `graea/visual/prompts.py`
- `graea/visual/vision.py`
- `tests/test_web.py`
- `tests/test_vision.py`

All tests pass with no Telegram credentials and no network:

```
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python -m pytest tests/test_vision.py tests/test_web.py -q
16 passed
```

`ruff check` is clean on all five files.

## WebK URL scheme

Used `https://web.telegram.org/k/#@BotFather` (hash-fragment with `@username`)
as the direct-chat-open URL for `open_chat()`. This is the well-documented
WebK deep-link scheme (`{web_url}#@username`, username without the leading
`@` stripped before the `#@` is re-added). **This is unverified against a
live logged-in session** — this agent has no Telegram credentials and no
network access in this environment. If it turns out wrong, the fix is
localized to `TelegramWeb.open_chat()`.

## Selectors — what's confirmed vs guessed

`graea/visual/web.py` centralizes all CSS selectors in `SELECTORS` at the
top of the file, several candidates per role, tried in order by
`_first_visible()` until one matches. **None of these have been checked
against a live WebK login** (no credentials/network here); they are informed
guesses based on WebK's known DOM conventions (BEM-ish `.bubble`,
`#column-left`/`#column-center` layout, `.reply-markup-button` for inline
keyboard buttons) and are very likely to need adjustment once someone can
run this against a real account:

| role | candidates (first match wins) | confidence |
|---|---|---|
| `chat_column` | `.bubbles`, `.bubbles-inner`, `#column-center .scrollable`, `#column-center` | guess — layout ids (`#column-center`) are long-standing WebK structure, but exact scroll-container class may differ by version |
| `message_bubble` | `.bubble.is-in, .bubble.is-out`, `.bubble`, `[class*='bubble']` | guess — `.bubble` naming is long-standing but attribute/class details (`is-in`/`is-out`) may have moved |
| `inline_button` | `.reply-markup-button`, `.inline-button`, `button.reply-markup-button`, `[class*='reply-markup'] button` | guess |
| `login_qr` | `.qr-container`, `.login-qr`, `canvas.qr-canvas`, `[class*='qr']` | guess |
| `chat_list` | `#column-left .chatlist`, `.chatlist`, `#column-left` | guess |
| `app_root` | `#page-chats`, `#app` | guess, currently unused fallback role kept for future use |

**Action needed by whoever has real Telegram creds**: run
`graea_status` / a manual login, open devtools on `web.telegram.org/k/`,
and confirm/patch the `SELECTORS` dict — that's the only place a WebK
redesign should require edits. `is_logged_in()` additionally falls back to a
body-text heuristic ("log in to telegram", "scan qr", "quick log in") if
neither the chat-list nor QR selectors match within their timeouts, so it
degrades to "assume logged out" rather than hanging.

## Public API

### `graea/visual/web.py`

```python
class TelegramWeb:
    def __init__(self, settings: Settings): ...
    async def start() -> None
    async def stop() -> None
    async def is_logged_in() -> bool
    async def login_qr_screenshot(path: str) -> str
    async def wait_for_login(timeout_s: int) -> bool
    async def open_chat(bot_username: str) -> None
    async def screenshot(path: str, region: Literal["chat","last_messages","full"]="chat",
                          last_n: int = 5) -> Screenshot
    async def scroll_to_bottom() -> None
    async def click_inline_button(text: str) -> bool
    async def visible_text() -> str

class NullWeb:
    # same method surface; every method raises RuntimeError("visual eye disabled")

SELECTORS: dict[str, list[str]]  # centralized selector candidates, patch here
```

`screenshot()` always waits `settings.web_settle_ms` before capturing, writes
the PNG to `path`, and returns a `graea.models.Screenshot` with sha256 +
width/height computed via Pillow. `region="last_messages"` computes the union
bounding box of the last `last_n` `.bubble`-like elements and falls back to
the whole chat column if no bubbles are found. All methods raise
`RuntimeError` with a descriptive message on failure — the engine is expected
to catch this and set `StepResult.screenshot_error` (per AGENT-RULES: never
silent).

### `graea/visual/prompts.py`

```python
def build_prompt(expected: dict | None = None) -> str
```

Produces the exact text sent to a vision model: step 1 asks for a plain
description (oldest message first, newest last); step 2 asks for exactly one
fenced ` ```json ` block with keys `messages_seen` and `issues` matching
`graea.models.VisionReading`, plus a worked example. When `expected` is
given (`expected_text`, `expected_buttons`, `last_action`) the prompt adds a
cross-check instruction asking the model to flag any discrepancy as an
`issues[].kind == "mismatch"`.

### `graea/visual/vision.py`

```python
class VisionReader(Protocol):
    name: str
    model: Optional[str]
    async def read(self, shot: Screenshot, expected: dict | None = None) -> VisionReading: ...

class OpenAICompatibleVision(base_url, model, api_key="", timeout_s=120, settings=None)
class AnthropicVision(api_key, model="claude-sonnet-4-5", timeout_s=120, settings=None)
class OcrVision()
class NullVision()

def parse_reading(raw_text: str) -> tuple[str, list[VisionSeenMessage], list[VisionFinding]]
def ocr_text(path: str) -> str | None
def make_reader(settings: Settings) -> VisionReader
async def probe(reader: VisionReader) -> bool
```

- `OpenAICompatibleVision.read()` POSTs `{base_url}/chat/completions` with an
  `image_url` content part carrying a base64 `data:image/png;base64,...` URL
  — works unmodified against Ollama's OpenAI-compatible endpoint, vLLM,
  LM Studio, OpenRouter, and OpenAI itself.
- `AnthropicVision.read()` POSTs `https://api.anthropic.com/v1/messages`
  directly via httpx (no `anthropic` SDK dependency) with an `image`
  content block (`source.type == "base64"`).
- `OcrVision.read()` shells out to `tesseract <path> stdout` (subprocess,
  `pytesseract` is intentionally not used per AGENT-RULES) and sets
  `description = "OCR only: <text>"`; a stray-markdown-character heuristic
  (`*word`, `_word_`) adds a `raw_markdown` issue.
- `NullVision.read()` always returns `VisionReading(provider="none",
  error="vision disabled")`.
- All four never raise: every failure path (bad image path, HTTP error,
  timeout, missing tesseract) is caught and returned as
  `VisionReading(error=...)`, per AGENT-RULES ("failures are never silent").
- OCR text is attached alongside *any* provider's reading (not just
  `OcrVision`) whenever `settings.ocr != "off"` and tesseract is available
  (`auto` checks `shutil.which("tesseract")`), per the SPEC's "OCR text ...
  is stored alongside regardless of provider."
- `parse_reading()` looks for a fenced ` ```json ` block first, falls back to
  the first/last `{`/`}` in the text, and on total failure to parse returns
  `(raw_text, [], [VisionFinding(kind="other", severity="info", detail="reader
  returned no JSON")])` — never raises.
- `probe(reader)` builds a synthetic 1x1 PNG in a temp file and calls
  `reader.read()`, returning `reading.error is None`. Used for
  `Health.vision_ok`.

## Known gaps / needs real creds or a live WebK session

1. **WebK selectors are unverified.** See table above — this is the single
   biggest risk item; a real login + devtools inspection is needed before
   trusting `is_logged_in`, `open_chat`, `screenshot(region=...)`, or
   `click_inline_button` against the real site. All are structured so a
   redesign only requires editing `SELECTORS`.
2. **The `#@username` deep-link scheme is unverified live** (see above).
3. `AnthropicVision` model default (`claude-sonnet-4-5`) is illustrative;
   the caller should pass `settings.vision_model` (via `make_reader`) in
   practice, which it does.
4. `OcrVision` against a 1x1 png (used by `probe()`) will typically return no
   text and thus `error` set — that's expected/correct behavior for a health
   probe against OCR-only mode (no LLM to "always answer"), not a bug.
5. Login-screen text heuristic strings ("log in to telegram", "scan qr",
   "quick log in") are best-effort guesses at WebK's copy and may need
   adjustment.
6. No test exercises real network calls to web.telegram.org, Ollama,
   OpenAI, or Anthropic — by design, per AGENT-RULES ("no Telegram
   credentials and no network").
