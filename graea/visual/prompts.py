"""Vision reader prompt builder.

Implements the "Vision reading contract" from docs/SPEC.md: ask the model for
a plain description first (newest message last), then a single fenced JSON
object with keys `messages_seen` and `issues`.
"""
from __future__ import annotations

import json
from typing import Optional

EXAMPLE_JSON = {
    "messages_seen": [
        {
            "sender": "bot",
            "text_as_rendered": "Welcome! Choose an option below.",
            "buttons": ["Option A", "Option B"],
            "media": None,
        }
    ],
    "issues": [
        {
            "severity": "medium",
            "kind": "truncated_button",
            "detail": "Button 'Option B' label appears cut off at the edge.",
        }
    ],
}

ISSUE_KINDS = (
    "raw_markdown", "truncated_button", "missing_caption", "empty_message",
    "broken_media", "layout", "mismatch", "loading", "error_banner", "other",
)


def build_prompt(expected: Optional[dict] = None) -> str:
    """Build the text prompt sent alongside the screenshot to a vision model.

    Returns a string instructing the model to (1) describe what it sees in
    the chat, oldest message first and newest message last, then (2) emit a
    single fenced ```json code block containing an object with exactly the
    keys `messages_seen` and `issues`, matching graea.models.VisionReading.
    If `expected` is given (expected_text, expected_buttons, last_action) the
    model is asked to cross-check the screenshot against it and report any
    difference as an issue of kind "mismatch".
    """
    lines: list[str] = []
    lines.append(
        "You are the visual QA reader for an automated Telegram bot test. "
        "You are shown a screenshot of the Telegram Web chat with a bot."
    )
    lines.append("")
    lines.append("Step 1 — Description:")
    lines.append(
        "Write a short plain-English description of everything visible in the "
        "chat column: each message bubble, who sent it (bot vs test user), any "
        "inline or reply keyboard buttons, media (photos/documents/etc.), and "
        "anything that looks broken or unrendered. List messages in the order "
        "they appear top-to-bottom, i.e. oldest message first and the newest "
        "message last."
    )
    lines.append("")
    lines.append("Step 2 — Structured findings:")
    lines.append(
        "After the description, output exactly one fenced code block "
        "(```json ... ```) containing a single JSON object with exactly two "
        "keys:"
    )
    lines.append(
        '  "messages_seen": a list of objects with keys "sender" '
        '("bot"|"user"|"unknown"), "text_as_rendered" (the text as a human '
        'would read it, i.e. after markdown/entities are applied — report '
        'literal stray "*", "_", or backtick characters if you see them '
        'un-rendered), "buttons" (list of visible button label strings, if '
        'any), and "media" (a short description string, or null).'
    )
    lines.append(
        '  "issues": a list of objects with keys "severity" '
        '("info"|"low"|"medium"|"high"), "kind" (one of: '
        + ", ".join(ISSUE_KINDS) + '), and "detail" (a short human-readable '
        "explanation)."
    )
    lines.append("")
    lines.append(
        "Only report an issue when something is actually wrong or suspicious; "
        "an empty issues list is expected and good when the chat looks correct."
    )
    lines.append("")
    lines.append("Example of the expected JSON block:")
    lines.append("```json")
    lines.append(json.dumps(EXAMPLE_JSON, indent=2))
    lines.append("```")

    if expected:
        lines.append("")
        lines.append(
            "Cross-check against what the test engine expected to happen, and "
            "add an issue with kind \"mismatch\" for any discrepancy:"
        )
        exp_text = expected.get("expected_text")
        exp_buttons = expected.get("expected_buttons")
        last_action = expected.get("last_action")
        if last_action:
            lines.append(f"  - Last action performed by the test: {last_action}")
        if exp_text:
            lines.append(f"  - Expected the chat to contain text: {exp_text!r}")
        if exp_buttons:
            lines.append(f"  - Expected these buttons to be visible: {exp_buttons!r}")

    return "\n".join(lines)
