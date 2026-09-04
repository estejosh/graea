"""Unit tests for the demo bot's bug toggles.

No Telegram connection needed: exercises the pure message/keyboard-building
functions in graea.demo.buggy_bot directly.
"""
from __future__ import annotations

from telegram.constants import ParseMode

from graea.demo.buggy_bot import (
    ALL_BUGS,
    active_bugs,
    build_menu_keyboard,
    build_start,
    build_start_keyboard,
    build_status,
    generate_status_photo_bytes,
)


def test_active_bugs_parses_env(monkeypatch):
    monkeypatch.setenv("BUGS", "raw_markdown, dead_button ,,edit_as_new")
    assert active_bugs() == {"raw_markdown", "dead_button", "edit_as_new"}


def test_active_bugs_empty(monkeypatch):
    monkeypatch.delenv("BUGS", raising=False)
    assert active_bugs() == set()


def test_all_bugs_names_are_stable():
    # Every name mentioned in SPEC.md / AGENT-RULES.md for the demo bot.
    assert set(ALL_BUGS) == {
        "raw_markdown",
        "truncated_button",
        "edit_as_new",
        "missing_caption",
        "silent_command",
        "dead_button",
        "wrong_keyboard_shape",
    }


def test_start_keyboard_default_shape_is_1x3():
    kb = build_start_keyboard(set())
    assert [len(r) for r in kb.inline_keyboard] == [3]
    labels = [b.text for b in kb.inline_keyboard[0]]
    assert labels == ["Order", "Status", "Help"]


def test_wrong_keyboard_shape_bug_makes_3x1():
    kb = build_start_keyboard({"wrong_keyboard_shape"})
    assert [len(r) for r in kb.inline_keyboard] == [1, 1, 1]


def test_truncated_button_bug_makes_60_char_label():
    kb = build_start_keyboard({"truncated_button"})
    first_label = kb.inline_keyboard[0][0].text
    assert len(first_label) == 60
    assert first_label.startswith("Order")


def test_truncated_button_off_keeps_short_label():
    kb = build_start_keyboard(set())
    assert kb.inline_keyboard[0][0].text == "Order"


def test_build_start_no_bugs_uses_markdown_v2():
    built = build_start(set())
    assert built["parse_mode"] == ParseMode.MARKDOWN_V2
    assert "*Graea Demo Bot*" in built["text"]
    # Exclamation mark and other MarkdownV2 specials outside the bold span
    # must be escaped.
    assert "\\!" in built["text"]


def test_build_start_raw_markdown_bug_sends_unescaped_with_no_parse_mode():
    built = build_start({"raw_markdown"})
    assert built["parse_mode"] is None
    assert built["text"] == "Welcome to *Graea Demo Bot*! Use the buttons below."


def test_build_status_default_has_caption():
    status = build_status(set())
    assert status["caption"] == "Current status"


def test_build_status_missing_caption_bug_has_none():
    status = build_status({"missing_caption"})
    assert status["caption"] is None


def test_menu_keyboard_shape():
    kb = build_menu_keyboard()
    rows = [[b.text for b in row] for row in kb.keyboard]
    assert rows == [["A", "B"], ["C"]]


def test_status_photo_bytes_is_a_png():
    data = generate_status_photo_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) > 0
