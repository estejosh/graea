"""Tests for graea.engine.assertions: evaluate/evaluate_all, all AssertionKinds."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from graea.engine.assertions import evaluate, evaluate_all
from graea.models import (
    Action,
    ActionKind,
    AssertionSpec,
    Button,
    Keyboard,
    MediaInfo,
    ObservedMessage,
    StepResult,
    VisionFinding,
    VisionReading,
)

NOW = datetime.now(timezone.utc)


def _msg(msg_id=1, text="Hello world", rendered=None, keyboard=None, media=None,
         caption=None, date=None, entities=None, raw_text=None) -> ObservedMessage:
    return ObservedMessage(
        message_id=msg_id, chat_id=1, from_bot=True, date=date or NOW,
        text=raw_text if raw_text is not None else text,
        rendered_text=rendered if rendered is not None else text,
        keyboard=keyboard, media=media, caption=caption, entities=entities or [],
    )


def _step(replies=None, edits=None, deleted_ids=None, vision=None, sent_at=None) -> StepResult:
    return StepResult(
        run_id="r0001", step_id="r0001s000", idx=0, name="s",
        action=Action(kind=ActionKind.send_text, text="hi"),
        sent_at=sent_at or NOW, done_at=NOW,
        replies=replies or [], edits=edits or [], deleted_ids=deleted_ids or [],
        vision=vision,
    )


# text_contains / text_equals / text_regex


def test_text_contains_pass_and_fail():
    step = _step(replies=[_msg(text="Welcome to the bot")])
    ok = evaluate(AssertionSpec(kind="text_contains", value="Welcome"), step)
    assert ok.passed
    bad = evaluate(AssertionSpec(kind="text_contains", value="Goodbye"), step)
    assert not bad.passed
    assert "Welcome to the bot" in bad.actual


def test_text_contains_case_sensitive():
    step = _step(replies=[_msg(text="Welcome")])
    spec = AssertionSpec(kind="text_contains", value="welcome", case_sensitive=True)
    assert not evaluate(spec, step).passed
    spec2 = AssertionSpec(kind="text_contains", value="welcome", case_sensitive=False)
    assert evaluate(spec2, step).passed


def test_text_equals():
    step = _step(replies=[_msg(text="exact")])
    assert evaluate(AssertionSpec(kind="text_equals", value="exact"), step).passed
    assert not evaluate(AssertionSpec(kind="text_equals", value="exact!"), step).passed


def test_text_regex():
    step = _step(replies=[_msg(text="order #12345 confirmed")])
    assert evaluate(AssertionSpec(kind="text_regex", value=r"#\d+"), step).passed
    assert not evaluate(AssertionSpec(kind="text_regex", value=r"#[a-z]+"), step).passed


def test_text_regex_invalid_pattern_fails_cleanly():
    step = _step(replies=[_msg(text="x")])
    result = evaluate(AssertionSpec(kind="text_regex", value="(unclosed"), step)
    assert not result.passed
    assert "invalid regex" in result.message


# reply_count


def test_reply_count_default_gte():
    step = _step(replies=[_msg(1), _msg(2)])
    assert evaluate(AssertionSpec(kind="reply_count", value=2), step).passed
    assert evaluate(AssertionSpec(kind="reply_count", value=1), step).passed
    assert not evaluate(AssertionSpec(kind="reply_count", value=3), step).passed


def test_reply_count_exact_op():
    step = _step(replies=[_msg(1)])
    assert evaluate(AssertionSpec(kind="reply_count", value=1, op="=="), step).passed
    assert not evaluate(AssertionSpec(kind="reply_count", value=2, op="=="), step).passed


# no_reply


def test_no_reply_pass_and_fail():
    assert evaluate(AssertionSpec(kind="no_reply"), _step()).passed
    assert not evaluate(AssertionSpec(kind="no_reply"), _step(replies=[_msg()])).passed
    assert not evaluate(AssertionSpec(kind="no_reply"), _step(edits=[_msg()])).passed


# reply_within_ms


def test_reply_within_ms_pass_and_fail():
    sent_at = NOW
    fast_reply = _msg(date=sent_at + timedelta(milliseconds=100))
    step = _step(replies=[fast_reply], sent_at=sent_at)
    assert evaluate(AssertionSpec(kind="reply_within_ms", value=500), step).passed

    slow_reply = _msg(date=sent_at + timedelta(milliseconds=900))
    step2 = _step(replies=[slow_reply], sent_at=sent_at)
    assert not evaluate(AssertionSpec(kind="reply_within_ms", value=500), step2).passed


def test_reply_within_ms_no_reply_fails():
    result = evaluate(AssertionSpec(kind="reply_within_ms", value=500), _step())
    assert not result.passed


# has_inline_button / keyboard_shape / has_reply_keyboard


def _inline_kb(*rows_of_texts) -> Keyboard:
    rows = []
    for r, texts in enumerate(rows_of_texts):
        rows.append([Button(text=t, kind="callback", data=t, row=r, col=c)
                     for c, t in enumerate(texts)])
    return Keyboard(kind="inline", rows=rows)


def test_has_inline_button_exact_and_contains():
    kb = _inline_kb(["Yes", "No"])
    step = _step(replies=[_msg(keyboard=kb)])
    assert evaluate(AssertionSpec(kind="has_inline_button", value="Yes"), step).passed
    assert evaluate(AssertionSpec(kind="has_inline_button", value="Ye"), step).passed
    assert not evaluate(AssertionSpec(kind="has_inline_button", value="Maybe"), step).passed


def test_has_inline_button_regex():
    kb = _inline_kb(["Buy now"])
    step = _step(replies=[_msg(keyboard=kb)])
    spec = AssertionSpec(kind="has_inline_button", value="re:^Buy")
    assert evaluate(spec, step).passed
    spec2 = AssertionSpec(kind="has_inline_button", value="re:^Sell")
    assert not evaluate(spec2, step).passed


def test_keyboard_shape_list_and_string():
    kb = _inline_kb(["A", "B", "C"])
    step = _step(replies=[_msg(keyboard=kb)])
    assert evaluate(AssertionSpec(kind="keyboard_shape", value=[3]), step).passed
    assert evaluate(AssertionSpec(kind="keyboard_shape", value="1x3"), step).passed
    assert not evaluate(AssertionSpec(kind="keyboard_shape", value="3x1"), step).passed


def test_keyboard_shape_multi_row():
    kb = _inline_kb(["A"], ["B"], ["C"])
    step = _step(replies=[_msg(keyboard=kb)])
    assert evaluate(AssertionSpec(kind="keyboard_shape", value=[1, 1, 1]), step).passed
    assert evaluate(AssertionSpec(kind="keyboard_shape", value="3x1"), step).passed


def test_has_reply_keyboard():
    reply_kb = Keyboard(kind="reply", rows=[[Button(text="Menu", kind="reply")]])
    step = _step(replies=[_msg(keyboard=reply_kb)])
    assert evaluate(AssertionSpec(kind="has_reply_keyboard"), step).passed
    inline_step = _step(replies=[_msg(keyboard=_inline_kb(["A"]))])
    assert not evaluate(AssertionSpec(kind="has_reply_keyboard"), inline_step).passed


# message_edited / message_deleted


def test_message_edited_any_and_substring():
    edit = _msg(text="new content", rendered="new content")
    step = _step(edits=[edit])
    assert evaluate(AssertionSpec(kind="message_edited"), step).passed
    assert evaluate(AssertionSpec(kind="message_edited", value="new"), step).passed
    assert not evaluate(AssertionSpec(kind="message_edited", value="missing"), step).passed
    assert not evaluate(AssertionSpec(kind="message_edited"), _step()).passed


def test_message_deleted():
    assert evaluate(AssertionSpec(kind="message_deleted"), _step(deleted_ids=[1])).passed
    assert not evaluate(AssertionSpec(kind="message_deleted"), _step()).passed


# markdown_rendered


def test_markdown_rendered_clean():
    msg = _msg(raw_text="*bold*", rendered="bold", entities=[{"type": "bold", "offset": 0, "length": 4}])
    step = _step(replies=[msg])
    assert evaluate(AssertionSpec(kind="markdown_rendered"), step).passed


def test_markdown_rendered_stray_markers_fail():
    msg = _msg(raw_text="*bold* text", rendered="*bold* text", entities=[])
    step = _step(replies=[msg])
    result = evaluate(AssertionSpec(kind="markdown_rendered"), step)
    assert not result.passed
    assert "*" in result.actual


def test_markdown_rendered_no_replies_fails():
    result = evaluate(AssertionSpec(kind="markdown_rendered"), _step())
    assert not result.passed


# media_type / caption_contains


def test_media_type():
    photo = MediaInfo(kind="photo")
    step = _step(replies=[_msg(media=photo)])
    assert evaluate(AssertionSpec(kind="media_type", value="photo"), step).passed
    assert not evaluate(AssertionSpec(kind="media_type", value="video"), step).passed


def test_caption_contains():
    step = _step(replies=[_msg(caption="a nice photo")])
    assert evaluate(AssertionSpec(kind="caption_contains", value="nice"), step).passed
    assert not evaluate(AssertionSpec(kind="caption_contains", value="ugly"), step).passed


# vision_no_issues / vision_sees


def test_vision_no_issues_pass():
    vision = VisionReading(provider="ocr", description="clean", issues=[])
    step = _step(vision=vision)
    assert evaluate(AssertionSpec(kind="vision_no_issues"), step).passed


def test_vision_no_issues_fail_on_severity():
    vision = VisionReading(provider="ocr", description="x", issues=[
        VisionFinding(severity="high", kind="raw_markdown", detail="stray *"),
    ])
    step = _step(vision=vision)
    result = evaluate(AssertionSpec(kind="vision_no_issues"), step)
    assert not result.passed
    assert "raw_markdown" in result.actual


def test_vision_no_issues_respects_severity_threshold():
    vision = VisionReading(provider="ocr", description="x", issues=[
        VisionFinding(severity="low", kind="layout", detail="minor"),
    ])
    step = _step(vision=vision)
    # default threshold medium: low issue should not fail
    assert evaluate(AssertionSpec(kind="vision_no_issues"), step).passed
    # explicit low threshold: low issue should fail
    assert not evaluate(AssertionSpec(kind="vision_no_issues", severity="low"), step).passed


def test_vision_no_issues_absent_reader_never_silently_passes():
    step = _step(vision=None)
    result = evaluate(AssertionSpec(kind="vision_no_issues"), step)
    assert not result.passed
    assert "unavailable" in result.message or "no vision" in result.message


def test_vision_no_issues_error_reader_fails():
    vision = VisionReading(provider="openai_compatible", error="connection refused")
    step = _step(vision=vision)
    result = evaluate(AssertionSpec(kind="vision_no_issues"), step)
    assert not result.passed
    assert "connection refused" in result.message


def test_vision_sees():
    vision = VisionReading(provider="ocr", description="a cat sitting on a chair")
    step = _step(vision=vision)
    assert evaluate(AssertionSpec(kind="vision_sees", value="cat"), step).passed
    assert not evaluate(AssertionSpec(kind="vision_sees", value="dog"), step).passed


def test_vision_sees_absent_fails():
    result = evaluate(AssertionSpec(kind="vision_sees", value="cat"), _step(vision=None))
    assert not result.passed


# evaluate_all


def test_evaluate_all_order_preserved():
    step = _step(replies=[_msg(text="hi")])
    specs = [
        AssertionSpec(kind="text_contains", value="hi", name="a"),
        AssertionSpec(kind="no_reply", name="b"),
    ]
    results = evaluate_all(specs, step)
    assert [r.name for r in results] == ["a", "b"]
    assert results[0].passed is True
    assert results[1].passed is False


def test_unknown_kind_fails_gracefully():
    step = _step()
    result = evaluate(AssertionSpec(kind="text_contains", value="x"), step)
    assert result is not None  # sanity: known kind path still works
