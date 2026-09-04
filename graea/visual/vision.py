"""Vision readers: turn a Screenshot into a VisionReading.

Public API:
    class VisionReader(Protocol) — async def read(self, shot, expected=None) -> VisionReading
    class OpenAICompatibleVision  — Ollama / vLLM / LM Studio / OpenRouter / OpenAI
    class AnthropicVision         — api.anthropic.com/v1/messages, no SDK
    class OcrVision                — tesseract-only fallback
    class NullVision                — vision disabled
    parse_reading(raw_text) -> (description, messages_seen, issues)
    ocr_text(path) -> str | None
    make_reader(settings) -> VisionReader
    async def probe(reader) -> bool

Never raises from read(): all failures come back as VisionReading(error=...).
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import time
from typing import Optional, Protocol, runtime_checkable

import httpx

from graea.config import Settings
from graea.models import Screenshot, VisionFinding, VisionReading, VisionSeenMessage
from graea.visual.prompts import build_prompt

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)

# A 1x1 transparent PNG, used by probe().
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_reading(raw_text: str) -> tuple[str, list[VisionSeenMessage], list[VisionFinding]]:
    """Parse a vision model's raw text response into (description, messages_seen, issues).

    Tolerant of missing/invalid JSON: if no valid fenced (or bare) JSON object
    with the expected keys is found, the whole raw_text becomes the
    description, messages_seen is [], and issues gets one info-level
    VisionFinding(kind="other") noting the reader returned no JSON.
    """
    raw_text = raw_text or ""
    match = _FENCE_RE.search(raw_text)
    json_str = match.group(1) if match else None

    if json_str is None:
        # Try to find a bare top-level JSON object as a fallback.
        brace_start = raw_text.find("{")
        brace_end = raw_text.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            json_str = raw_text[brace_start:brace_end + 1]

    description = raw_text.strip()
    if match:
        # Description is everything before the fenced block.
        description = raw_text[:match.start()].strip() or raw_text.strip()

    if json_str:
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            data = None
    else:
        data = None

    if not isinstance(data, dict):
        return (
            description,
            [],
            [VisionFinding(severity="info", kind="other", detail="reader returned no JSON")],
        )

    messages_seen: list[VisionSeenMessage] = []
    for m in data.get("messages_seen", []) or []:
        if not isinstance(m, dict):
            continue
        try:
            messages_seen.append(VisionSeenMessage(
                sender=m.get("sender", "unknown") or "unknown",
                text_as_rendered=m.get("text_as_rendered", "") or "",
                buttons=m.get("buttons", []) or [],
                media=m.get("media"),
            ))
        except Exception:
            continue

    issues: list[VisionFinding] = []
    for i in data.get("issues", []) or []:
        if not isinstance(i, dict):
            continue
        try:
            issues.append(VisionFinding(
                severity=i.get("severity", "medium") or "medium",
                kind=i.get("kind", "other") or "other",
                detail=i.get("detail", "") or "",
            ))
        except Exception:
            continue

    return description, messages_seen, issues


def ocr_text(path: str) -> Optional[str]:
    """Run `tesseract <path> stdout` and return the extracted text, or None
    if tesseract is not installed or the call fails."""
    if not shutil.which("tesseract"):
        return None
    try:
        proc = subprocess.run(
            ["tesseract", path, "stdout"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None
    except Exception:
        return None


def _ocr_issues_from_text(text: str) -> list[VisionFinding]:
    """Heuristic issues derived from raw OCR text (stray markdown chars)."""
    issues: list[VisionFinding] = []
    if re.search(r"(\*[^\s*]|\b_[^\s_]|[^\s_]_\b)", text):
        issues.append(VisionFinding(
            severity="low", kind="raw_markdown",
            detail="OCR text contains stray markdown characters (*, _)",
        ))
    return issues


def _should_ocr(settings: Optional[Settings]) -> bool:
    if settings is None:
        return shutil.which("tesseract") is not None
    if settings.ocr == "off":
        return False
    if settings.ocr == "on":
        return True
    return shutil.which("tesseract") is not None  # auto


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------


@runtime_checkable
class VisionReader(Protocol):
    name: str
    model: Optional[str]

    async def read(self, shot: Screenshot, expected: Optional[dict] = None) -> VisionReading: ...


# --------------------------------------------------------------------------
# Implementations
# --------------------------------------------------------------------------


class OpenAICompatibleVision:
    """Works with Ollama, vLLM, LM Studio, OpenRouter, OpenAI: POST
    {base_url}/chat/completions with an image_url data URL."""

    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout_s: int = 120, settings: Optional[Settings] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.settings = settings

    async def read(self, shot: Screenshot, expected: Optional[dict] = None) -> VisionReading:
        started = time.monotonic()
        text_prompt = build_prompt(expected)
        try:
            with open(shot.path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            return VisionReading(provider=self.name, model=self.model,
                                  error=f"could not read screenshot: {e}")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text_prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    ],
                }
            ],
            "max_tokens": 1024,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        raw_text = ""
        error: Optional[str] = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(f"{self.base_url}/chat/completions",
                                          json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                raw_text = data["choices"][0]["message"]["content"]
        except Exception as e:
            error = f"{self.name} vision request failed: {e}"

        latency_ms = int((time.monotonic() - started) * 1000)
        if error:
            return VisionReading(provider=self.name, model=self.model,
                                  latency_ms=latency_ms, error=error,
                                  ocr_text=ocr_text(shot.path) if _should_ocr(self.settings) else None)

        description, messages_seen, issues = parse_reading(raw_text)
        return VisionReading(
            provider=self.name, model=self.model, description=description,
            messages_seen=messages_seen, issues=issues,
            ocr_text=ocr_text(shot.path) if _should_ocr(self.settings) else None,
            latency_ms=latency_ms,
        )


class AnthropicVision:
    """POST api.anthropic.com/v1/messages with an image content block. No SDK."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5",
                 timeout_s: int = 120, settings: Optional[Settings] = None):
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.settings = settings
        self.base_url = "https://api.anthropic.com/v1/messages"

    async def read(self, shot: Screenshot, expected: Optional[dict] = None) -> VisionReading:
        started = time.monotonic()
        text_prompt = build_prompt(expected)
        try:
            with open(shot.path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            return VisionReading(provider=self.name, model=self.model,
                                  error=f"could not read screenshot: {e}")

        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64",
                                                       "media_type": "image/png",
                                                       "data": img_b64}},
                        {"type": "text", "text": text_prompt},
                    ],
                }
            ],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        raw_text = ""
        error: Optional[str] = None
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(self.base_url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                parts = data.get("content", [])
                raw_text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        except Exception as e:
            error = f"anthropic vision request failed: {e}"

        latency_ms = int((time.monotonic() - started) * 1000)
        if error:
            return VisionReading(provider=self.name, model=self.model,
                                  latency_ms=latency_ms, error=error,
                                  ocr_text=ocr_text(shot.path) if _should_ocr(self.settings) else None)

        description, messages_seen, issues = parse_reading(raw_text)
        return VisionReading(
            provider=self.name, model=self.model, description=description,
            messages_seen=messages_seen, issues=issues,
            ocr_text=ocr_text(shot.path) if _should_ocr(self.settings) else None,
            latency_ms=latency_ms,
        )


class OcrVision:
    """tesseract-only fallback: no LLM, description = "OCR only: <text>"."""

    name = "ocr"
    model = None

    async def read(self, shot: Screenshot, expected: Optional[dict] = None) -> VisionReading:
        started = time.monotonic()
        text = ocr_text(shot.path)
        latency_ms = int((time.monotonic() - started) * 1000)
        if text is None:
            return VisionReading(provider=self.name, model=None, latency_ms=latency_ms,
                                  error="tesseract not available or OCR failed")
        issues = _ocr_issues_from_text(text)
        return VisionReading(
            provider=self.name, model=None,
            description=f"OCR only: {text}",
            messages_seen=[],
            issues=issues,
            ocr_text=text,
            latency_ms=latency_ms,
        )


class NullVision:
    """Vision disabled."""

    name = "none"
    model = None

    async def read(self, shot: Screenshot, expected: Optional[dict] = None) -> VisionReading:
        return VisionReading(provider=self.name, model=None, error="vision disabled")


# --------------------------------------------------------------------------
# Factory / health
# --------------------------------------------------------------------------


def make_reader(settings: Settings) -> VisionReader:
    """Build the configured VisionReader from Settings.vision_provider."""
    provider = settings.vision_provider
    if provider == "openai_compatible":
        return OpenAICompatibleVision(
            base_url=settings.vision_base_url, model=settings.vision_model,
            api_key=settings.vision_api_key, timeout_s=settings.vision_timeout_s,
            settings=settings,
        )
    if provider == "anthropic":
        return AnthropicVision(
            api_key=settings.vision_api_key, model=settings.vision_model,
            timeout_s=settings.vision_timeout_s, settings=settings,
        )
    if provider == "ocr":
        return OcrVision()
    return NullVision()


async def probe(reader: VisionReader) -> bool:
    """Health check: run the reader against a tiny synthetic screenshot.

    Returns True iff the reader returns a reading with no error set.
    """
    import hashlib
    import tempfile
    import os

    png_bytes = base64.b64decode(_TINY_PNG_B64)
    fd, path = tempfile.mkstemp(suffix=".png")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(png_bytes)
        shot = Screenshot(
            path=path, sha256=hashlib.sha256(png_bytes).hexdigest(),
            width=1, height=1, region="full",
        )
        reading = await reader.read(shot)
        return reading.error is None
    except Exception:
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
