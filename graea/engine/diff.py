"""Diffing: step-vs-previous-step and run-vs-run comparisons for the LLM.

Public API:
    diff_steps(current, previous) -> StepDiff
    diff_runs(store, run_a, run_b) -> DiffReport
    run_progress(store, run_id) -> str
"""
from __future__ import annotations

from typing import Optional

from graea.models import DiffReport, ObservedMessage, StepDiff, StepResult


def _reply_text_blob(replies: list[ObservedMessage]) -> str:
    return "\n".join(m.rendered_text or "" for m in replies)


def _keyboard_signature(replies: list[ObservedMessage]) -> list[tuple]:
    sigs = []
    for m in replies:
        if not m.keyboard:
            continue
        shape = tuple(m.keyboard.shape)
        texts = tuple(b.text for b in m.keyboard.flat())
        sigs.append((m.keyboard.kind, shape, texts))
    return sigs


def diff_steps(current: StepResult, previous: Optional[StepResult]) -> StepDiff:
    """Diff one step's assertions/text/keyboard/vision against the same-named
    step from the previous run. `previous=None` means this is the first run
    of this step: every current assertion is reported as new.
    """
    cur_map = {a.name: a.passed for a in current.assertions}

    if previous is None:
        return StepDiff(
            previous_run_id=None,
            previous_step_id=None,
            new_assertions=list(cur_map.keys()),
            text_changed=False,
            keyboard_changed=False,
            vision_issue_delta=0,
            summary=f"step '{current.name}': no previous run to compare against "
                    f"({len(cur_map)} assertion(s))",
        )

    prev_map = {a.name: a.passed for a in previous.assertions}

    fixed = [n for n, p in cur_map.items() if n in prev_map and not prev_map[n] and p]
    regressed = [n for n, p in cur_map.items() if n in prev_map and prev_map[n] and not p]
    still_failing = [n for n, p in cur_map.items() if n in prev_map and not prev_map[n] and not p]
    still_passing = [n for n, p in cur_map.items() if n in prev_map and prev_map[n] and p]
    new_assertions = [n for n in cur_map if n not in prev_map]

    text_changed = _reply_text_blob(current.replies) != _reply_text_blob(previous.replies)
    keyboard_changed = _keyboard_signature(current.replies) != _keyboard_signature(previous.replies)

    cur_issues = len(current.vision.issues) if current.vision else 0
    prev_issues = len(previous.vision.issues) if previous.vision else 0
    vision_issue_delta = cur_issues - prev_issues

    details = []
    if fixed:
        details.append(f"{len(fixed)} fixed")
    if regressed:
        details.append(f"{len(regressed)} regressed")
    if still_failing:
        details.append(f"{len(still_failing)} still failing")
    if still_passing:
        details.append(f"{len(still_passing)} still passing")
    if new_assertions:
        details.append(f"{len(new_assertions)} new")
    if text_changed:
        details.append("text changed")
    if keyboard_changed:
        details.append("keyboard changed")
    if vision_issue_delta:
        details.append(f"vision issues {'+' if vision_issue_delta > 0 else ''}{vision_issue_delta}")
    summary = f"step '{current.name}': " + (", ".join(details) if details else "no change")

    return StepDiff(
        previous_run_id=previous.run_id,
        previous_step_id=previous.step_id,
        fixed=fixed,
        regressed=regressed,
        still_failing=still_failing,
        still_passing=still_passing,
        new_assertions=new_assertions,
        text_changed=text_changed,
        keyboard_changed=keyboard_changed,
        vision_issue_delta=vision_issue_delta,
        summary=summary,
    )


def diff_runs(store, run_a: str, run_b: str) -> DiffReport:
    """Diff two runs of the same scenario, matching steps by name.

    run_a is the baseline, run_b is the run being evaluated against it.
    Assertion names in the result lists are prefixed "<step name>/<assertion name>"
    since the same assertion name can recur across steps.
    """
    summary_a = store.get_run(run_a)
    summary_b = store.get_run(run_b)

    steps_a = {s.name: s for s in summary_a.step_results}
    steps_b = {s.name: s for s in summary_b.step_results}

    fixed: list[str] = []
    regressed: list[str] = []
    still_failing: list[str] = []
    still_passing: list[str] = []
    new_assertions: list[str] = []

    for name, step_b in steps_b.items():
        step_a = steps_a.get(name)
        sd = diff_steps(step_b, step_a)
        fixed.extend(f"{name}/{n}" for n in sd.fixed)
        regressed.extend(f"{name}/{n}" for n in sd.regressed)
        still_failing.extend(f"{name}/{n}" for n in sd.still_failing)
        still_passing.extend(f"{name}/{n}" for n in sd.still_passing)
        new_assertions.extend(f"{name}/{n}" for n in sd.new_assertions)

    missing_steps = [name for name in steps_a if name not in steps_b]

    summary = (
        f"{summary_a.scenario}: {run_a} -> {run_b}: "
        f"{len(fixed)} fixed, {len(regressed)} regressed, "
        f"{len(still_failing)} still failing, {len(still_passing)} still passing"
    )
    if missing_steps:
        summary += f", {len(missing_steps)} step(s) missing in {run_b}"

    return DiffReport(
        scenario=summary_a.scenario, run_a=run_a, run_b=run_b,
        fingerprint_a=summary_a.fingerprint, fingerprint_b=summary_b.fingerprint,
        fixed=fixed, regressed=regressed, still_failing=still_failing,
        still_passing=still_passing, new_assertions=new_assertions,
        missing_steps=missing_steps, summary=summary,
    )


def run_progress(store, run_id: str) -> str:
    """One-line progress summary for a run vs the previous run of its scenario."""
    return store.progress_line(run_id)
