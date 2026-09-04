"""Assertion evaluators.

Public API:
    evaluate(spec, step) -> AssertionResult
    evaluate_all(specs, step) -> list[AssertionResult]

Every evaluator sets `actual` to a compact string describing what was really
found, so an LLM reading the result can reason about a failure without
re-running the step.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from graea.models import (
    AssertionResult,
    AssertionSpec,
    ObservedMessage,
    StepResult,
)

_SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}


def _texts(step: StepResult) -> list[tuple[ObservedMessage, str]]:
    """(message, searchable text) pairs: rendered_text plus caption, for each reply."""
    out: list[tuple[ObservedMessage, str]] = []
    for m in step.replies:
        blob = m.rendered_text or ""
        if m.caption:
            blob = f"{blob}\n{m.caption}" if blob else m.caption
        out.append((m, blob))
    return out


def _norm(s: str, case_sensitive: bool) -> str:
    return s if case_sensitive else s.lower()


def _fmt_texts(step: StepResult) -> str:
    blobs = [t for _, t in _texts(step)]
    if not blobs:
        return "no replies"
    return " | ".join(repr(b) for b in blobs)


def _eval_text_contains(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    value = str(spec.value or "")
    needle = _norm(value, spec.case_sensitive)
    found = any(needle in _norm(t, spec.case_sensitive) for _, t in _texts(step))
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=found,
        actual=_fmt_texts(step),
        message="" if found else f"none of the replies contain {value!r}",
    )


def _eval_text_equals(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    value = str(spec.value or "")
    target = _norm(value, spec.case_sensitive)
    found = any(_norm(t, spec.case_sensitive) == target for _, t in _texts(step))
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=found,
        actual=_fmt_texts(step),
        message="" if found else f"no reply equals {value!r}",
    )


def _eval_text_regex(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    pattern = str(spec.value or "")
    flags = 0 if spec.case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual=_fmt_texts(step), message=f"invalid regex {pattern!r}: {e}",
        )
    found = any(rx.search(t) for _, t in _texts(step))
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=found,
        actual=_fmt_texts(step),
        message="" if found else f"no reply matches /{pattern}/",
    )


_OPS: dict[str, Callable[[int, int], bool]] = {
    "==": lambda a, b: a == b,
    "=": lambda a, b: a == b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "!=": lambda a, b: a != b,
}


def _eval_reply_count(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    op = spec.op or ">="
    fn = _OPS.get(op)
    count = len(step.replies)
    if fn is None:
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual=str(count), message=f"unknown op {op!r}",
        )
    try:
        expected = int(spec.value)
    except (TypeError, ValueError):
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual=str(count), message=f"invalid value {spec.value!r}, expected int",
        )
    passed = fn(count, expected)
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed,
        actual=f"reply_count={count}",
        message="" if passed else f"expected count {op} {expected}, got {count}",
    )


def _eval_no_reply(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    passed = not step.replies and not step.edits
    actual = f"replies={len(step.replies)} edits={len(step.edits)}"
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed, actual=actual,
        message="" if passed else "expected silence but got activity",
    )


def _eval_reply_within_ms(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    try:
        budget = int(spec.value)
    except (TypeError, ValueError):
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual="n/a", message=f"invalid value {spec.value!r}, expected int ms",
        )
    if not step.replies:
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual="no replies", message="no reply arrived",
        )
    first = min(step.replies, key=lambda m: m.date)
    delta_ms = (first.date - step.sent_at).total_seconds() * 1000.0
    passed = delta_ms <= budget
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed,
        actual=f"{delta_ms:.0f}ms",
        message="" if passed else f"first reply took {delta_ms:.0f}ms, budget {budget}ms",
    )


def _button_matches(text: str, value: str, case_sensitive: bool) -> bool:
    if value.startswith("re:"):
        pattern = value[3:]
        flags = 0 if case_sensitive else re.IGNORECASE
        return bool(re.search(pattern, text, flags))
    a, b = _norm(text, case_sensitive), _norm(value, case_sensitive)
    return a == b or b in a


def _eval_has_inline_button(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    value = str(spec.value or "")
    found_texts: list[str] = []
    matched = False
    for m in step.replies:
        if not m.keyboard or m.keyboard.kind != "inline":
            continue
        for b in m.keyboard.flat():
            found_texts.append(b.text)
            if _button_matches(b.text, value, spec.case_sensitive):
                matched = True
    actual = f"buttons={found_texts}" if found_texts else "no inline keyboard"
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=matched, actual=actual,
        message="" if matched else f"no inline button matching {value!r}",
    )


def _parse_shape(value) -> Optional[list[int]]:
    if isinstance(value, list):
        try:
            return [int(x) for x in value]
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        m = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", value, re.IGNORECASE)
        if m:
            rows, cols = int(m.group(1)), int(m.group(2))
            return [cols] * rows
    return None


def _eval_keyboard_shape(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    expected = _parse_shape(spec.value)
    if expected is None:
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual="n/a", message=f"could not parse expected shape {spec.value!r}",
        )
    shapes = []
    for m in step.replies:
        if m.keyboard and m.keyboard.kind == "inline":
            shapes.append(m.keyboard.shape)
    passed = expected in shapes
    actual = f"shapes={shapes}" if shapes else "no inline keyboard"
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed, actual=actual,
        message="" if passed else f"expected shape {expected}, found {shapes}",
    )


def _eval_has_reply_keyboard(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    found = any(m.keyboard and m.keyboard.kind == "reply" for m in step.replies)
    kinds = [m.keyboard.kind for m in step.replies if m.keyboard]
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=found,
        actual=f"keyboards={kinds}" if kinds else "no keyboard",
        message="" if found else "no reply keyboard found",
    )


def _eval_message_edited(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    edits = step.edits
    if spec.value:
        needle = _norm(str(spec.value), spec.case_sensitive)
        matching = [e for e in edits if needle in _norm(e.rendered_text or "", spec.case_sensitive)]
    else:
        matching = edits
    passed = bool(matching)
    actual = f"edits={[e.rendered_text for e in edits]}" if edits else "no edits"
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed, actual=actual,
        message="" if passed else "no matching edit found",
    )


def _eval_message_deleted(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    passed = bool(step.deleted_ids)
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed,
        actual=f"deleted_ids={step.deleted_ids}",
        message="" if passed else "no messages were deleted",
    )


_MD_MARKER_RE = re.compile(r"(\*\*?|__?|`{1,3}|\[[^\]]*\]\([^)]*\))")


def _stray_markers(text: str) -> list[str]:
    found = []
    for marker in ("*", "_", "`"):
        count = text.count(marker)
        if count % 2 != 0:
            found.append(marker)
    if re.search(r"\[[^\]]*\]\([^)]*\)", text):
        found.append("[text](url)")
    return found


def _eval_markdown_rendered(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    if not step.replies:
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual="no replies", message="no replies to check",
        )
    problems: list[str] = []
    for m in step.replies:
        rendered = m.rendered_text or ""
        stray = _stray_markers(rendered)
        if stray:
            problems.append(f"stray markers {stray} in {rendered!r}")
        raw = m.text or ""
        raw_has_markers = bool(_MD_MARKER_RE.search(raw))
        if raw_has_markers and not m.entities:
            problems.append(f"raw text had markdown markers but no entities: {raw!r}")
    passed = not problems
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed,
        actual="; ".join(problems) if problems else "clean",
        message="" if passed else "; ".join(problems),
    )


def _eval_media_type(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    value = str(spec.value or "")
    kinds = [m.media.kind for m in step.replies if m.media]
    passed = value in kinds
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed,
        actual=f"media_kinds={kinds}" if kinds else "no media",
        message="" if passed else f"no reply with media kind {value!r}",
    )


def _eval_caption_contains(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    value = str(spec.value or "")
    needle = _norm(value, spec.case_sensitive)
    captions = [m.caption for m in step.replies if m.caption]
    passed = any(needle in _norm(c, spec.case_sensitive) for c in captions)
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed,
        actual=f"captions={captions}" if captions else "no captions",
        message="" if passed else f"no caption contains {value!r}",
    )


def _eval_vision_no_issues(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    vision = step.vision
    if vision is None or not vision.available:
        reason = "no vision reading attached" if vision is None else (vision.error or "vision reader unavailable")
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual="vision unavailable", message=reason,
        )
    max_severity = spec.severity or "medium"
    threshold = _SEVERITY_RANK.get(max_severity, 2)
    bad = [f for f in vision.issues if _SEVERITY_RANK.get(f.severity, 2) >= threshold]
    passed = not bad
    actual = "; ".join(f"{f.severity}/{f.kind}: {f.detail}" for f in bad) if bad else "no issues"
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed, actual=actual,
        message="" if passed else f"{len(bad)} issue(s) at or above {max_severity}",
    )


def _eval_vision_sees(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    vision = step.vision
    if vision is None or not vision.available:
        reason = "no vision reading attached" if vision is None else (vision.error or "vision reader unavailable")
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual="vision unavailable", message=reason,
        )
    value = str(spec.value or "")
    needle = _norm(value, spec.case_sensitive)
    haystacks = [vision.description or "", vision.ocr_text or ""]
    passed = any(needle in _norm(h, spec.case_sensitive) for h in haystacks)
    return AssertionResult(
        name=spec.label(), kind=spec.kind, passed=passed,
        actual=f"description={vision.description!r} ocr_text={vision.ocr_text!r}",
        message="" if passed else f"vision reading does not mention {value!r}",
    )


_EVALUATORS: dict[str, Callable[[AssertionSpec, StepResult], AssertionResult]] = {
    "text_contains": _eval_text_contains,
    "text_equals": _eval_text_equals,
    "text_regex": _eval_text_regex,
    "reply_count": _eval_reply_count,
    "no_reply": _eval_no_reply,
    "reply_within_ms": _eval_reply_within_ms,
    "has_inline_button": _eval_has_inline_button,
    "keyboard_shape": _eval_keyboard_shape,
    "has_reply_keyboard": _eval_has_reply_keyboard,
    "message_edited": _eval_message_edited,
    "message_deleted": _eval_message_deleted,
    "markdown_rendered": _eval_markdown_rendered,
    "media_type": _eval_media_type,
    "caption_contains": _eval_caption_contains,
    "vision_no_issues": _eval_vision_no_issues,
    "vision_sees": _eval_vision_sees,
}


def evaluate(spec: AssertionSpec, step: StepResult) -> AssertionResult:
    """Evaluate one AssertionSpec against a StepResult.

    Returns an AssertionResult with `actual` always populated with a compact
    description of what was really found, so an LLM can reason about a
    failure without re-running the step.
    """
    fn = _EVALUATORS.get(spec.kind)
    if fn is None:
        return AssertionResult(
            name=spec.label(), kind=spec.kind, passed=False,
            actual="n/a", message=f"unknown assertion kind {spec.kind!r}",
        )
    return fn(spec, step)


def evaluate_all(specs: list[AssertionSpec], step: StepResult) -> list[AssertionResult]:
    """Evaluate a list of AssertionSpecs against a StepResult, in order."""
    return [evaluate(spec, step) for spec in specs]
