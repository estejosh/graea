"""Tests for graea.visual.vision — offline, no network, no Telegram creds."""
from __future__ import annotations

import hashlib
import json
import shutil

import httpx
import pytest
from PIL import Image, ImageDraw

from graea.config import Settings
from graea.models import Screenshot
from graea.visual.vision import (
    AnthropicVision,
    NullVision,
    OcrVision,
    OpenAICompatibleVision,
    make_reader,
    parse_reading,
    probe,
)

HAS_TESSERACT = shutil.which("tesseract") is not None


def _make_screenshot(path, width=100, height=40) -> Screenshot:
    data = path.read_bytes()
    return Screenshot(
        path=str(path), sha256=hashlib.sha256(data).hexdigest(),
        width=width, height=height, region="full",
    )


# --------------------------------------------------------------------------
# parse_reading
# --------------------------------------------------------------------------


def test_parse_reading_good_json():
    raw = json.dumps({
        "messages_seen": [{"sender": "bot", "text_as_rendered": "hi",
                            "buttons": ["A"], "media": None}],
        "issues": [{"severity": "low", "kind": "layout", "detail": "cramped"}],
    })
    description, messages, issues = parse_reading(raw)
    assert len(messages) == 1
    assert messages[0].sender == "bot"
    assert messages[0].text_as_rendered == "hi"
    assert messages[0].buttons == ["A"]
    assert len(issues) == 1
    assert issues[0].kind == "layout"


def test_parse_reading_fenced_json():
    payload = {"messages_seen": [], "issues": []}
    raw = "Here is what I see: a plain welcome message.\n\n```json\n" + json.dumps(payload) + "\n```\n"
    description, messages, issues = parse_reading(raw)
    assert "welcome message" in description
    assert messages == []
    assert issues == []


def test_parse_reading_no_json():
    raw = "I could not produce structured output, sorry."
    description, messages, issues = parse_reading(raw)
    assert description == raw
    assert messages == []
    assert len(issues) == 1
    assert issues[0].kind == "other"
    assert "no JSON" in issues[0].detail


def test_parse_reading_empty_string():
    description, messages, issues = parse_reading("")
    assert description == ""
    assert messages == []
    assert len(issues) == 1


# --------------------------------------------------------------------------
# OpenAICompatibleVision (fake transport)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_compatible_vision_success(tmp_path):
    png_path = tmp_path / "shot.png"
    Image.new("RGB", (20, 20), "white").save(png_path)
    shot = _make_screenshot(png_path, 20, 20)

    canned = {
        "choices": [{"message": {"content":
            "A welcome message is visible.\n```json\n" +
            json.dumps({"messages_seen": [], "issues": []}) + "\n```"}}]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json=canned)

    transport = httpx.MockTransport(handler)
    reader = OpenAICompatibleVision(base_url="http://fake/v1", model="test-model",
                                     api_key="k", timeout_s=5)

    orig_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_client(*args, **kwargs)

    import graea.visual.vision as vision_mod
    vision_mod.httpx.AsyncClient = patched_client
    try:
        reading = await reader.read(shot)
    finally:
        vision_mod.httpx.AsyncClient = orig_client

    assert reading.error is None
    assert reading.provider == "openai_compatible"
    assert "welcome message" in reading.description
    assert reading.latency_ms is not None


@pytest.mark.asyncio
async def test_openai_compatible_vision_http_error(tmp_path):
    png_path = tmp_path / "shot.png"
    Image.new("RGB", (10, 10), "white").save(png_path)
    shot = _make_screenshot(png_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    reader = OpenAICompatibleVision(base_url="http://fake/v1", model="m", api_key="k")

    import graea.visual.vision as vision_mod
    orig_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_client(*args, **kwargs)

    vision_mod.httpx.AsyncClient = patched_client
    try:
        reading = await reader.read(shot)
    finally:
        vision_mod.httpx.AsyncClient = orig_client

    assert reading.error is not None
    assert reading.provider == "openai_compatible"


# --------------------------------------------------------------------------
# AnthropicVision (fake transport)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_vision_success(tmp_path):
    png_path = tmp_path / "shot.png"
    Image.new("RGB", (20, 20), "white").save(png_path)
    shot = _make_screenshot(png_path, 20, 20)

    canned = {
        "content": [{"type": "text", "text":
            "Nothing unusual.\n```json\n" +
            json.dumps({"messages_seen": [], "issues": []}) + "\n```"}]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.anthropic.com"
        return httpx.Response(200, json=canned)

    transport = httpx.MockTransport(handler)
    reader = AnthropicVision(api_key="k", model="claude-test", timeout_s=5)

    import graea.visual.vision as vision_mod
    orig_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_client(*args, **kwargs)

    vision_mod.httpx.AsyncClient = patched_client
    try:
        reading = await reader.read(shot)
    finally:
        vision_mod.httpx.AsyncClient = orig_client

    assert reading.error is None
    assert reading.provider == "anthropic"
    assert "Nothing unusual" in reading.description


# --------------------------------------------------------------------------
# OcrVision
# --------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract not installed")
@pytest.mark.asyncio
async def test_ocr_vision_on_generated_png(tmp_path):
    png_path = tmp_path / "ocr.png"
    img = Image.new("RGB", (400, 100), "white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 30), "hello *bold*", fill="black")
    img.save(png_path)
    shot = _make_screenshot(png_path, 400, 100)

    reader = OcrVision()
    reading = await reader.read(shot)

    assert reading.provider == "ocr"
    if reading.error is None:
        assert reading.description.startswith("OCR only:")
        assert reading.ocr_text is not None


@pytest.mark.asyncio
async def test_ocr_vision_no_tesseract(tmp_path, monkeypatch):
    png_path = tmp_path / "shot.png"
    Image.new("RGB", (10, 10), "white").save(png_path)
    shot = _make_screenshot(png_path)

    import graea.visual.vision as vision_mod
    monkeypatch.setattr(vision_mod.shutil, "which", lambda name: None)

    reader = OcrVision()
    reading = await reader.read(shot)
    assert reading.error is not None
    assert reading.provider == "ocr"


# --------------------------------------------------------------------------
# NullVision
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_vision_error_set(tmp_path):
    png_path = tmp_path / "shot.png"
    Image.new("RGB", (10, 10), "white").save(png_path)
    shot = _make_screenshot(png_path)

    reader = NullVision()
    reading = await reader.read(shot)
    assert reading.provider == "none"
    assert reading.error == "vision disabled"
    assert reading.available is False


# --------------------------------------------------------------------------
# make_reader / probe
# --------------------------------------------------------------------------


def test_make_reader_selects_by_provider():
    s = Settings(vision_provider="openai_compatible", vision_base_url="http://x/v1",
                  vision_model="m", vision_api_key="k")
    assert isinstance(make_reader(s), OpenAICompatibleVision)

    s2 = Settings(vision_provider="anthropic", vision_api_key="k", vision_model="claude-x")
    assert isinstance(make_reader(s2), AnthropicVision)

    s3 = Settings(vision_provider="ocr")
    assert isinstance(make_reader(s3), OcrVision)

    s4 = Settings(vision_provider="none")
    assert isinstance(make_reader(s4), NullVision)


@pytest.mark.asyncio
async def test_probe_null_vision_is_false():
    assert await probe(NullVision()) is False
