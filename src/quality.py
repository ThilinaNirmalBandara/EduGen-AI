"""Pre-render educational and presentation-quality checks."""

from __future__ import annotations

import math
from typing import Any
from .content import content_issues


class LessonQualityError(ValueError):
    """Raised when a generated plan is too shallow or visually unsuitable."""

    def __init__(self, message: str, *, issues: list[str] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []

    @property
    def narration_only(self) -> bool:
        return bool(self.issues) and all(
            issue.startswith("narration has ") or issue == "every scene must contain narration_text"
            for issue in self.issues
        )


def validate_lesson_quality(plan: dict, target_minutes: float, *, prompt: str = "",
                            require_summary: bool = False) -> None:
    scenes = plan.get("scenes", [])
    issues: list[str] = content_issues(plan)
    target_seconds = target_minutes * 60
    total_estimated = sum(float(scene["duration"]) for scene in scenes)
    words = sum(len(scene.get("narration_text", "").split()) for scene in scenes)
    # The renderer dispatches on type; metadata alone cannot turn bullets into a graph.
    visual_types = [scene.get("type") for scene in scenes]
    static_types = {"title", "bullets", "summary"}
    non_static = sum(visual not in static_types for visual in visual_types)
    lower_prompt = prompt.lower()
    mathematical = any(term in lower_prompt for term in (
        "gradient", "derivative", "equation", "calculus", "math", "function", "loss",
    ))

    if not scenes:
        issues.append("the lesson has no scenes")
    if require_summary and (not scenes or scenes[-1]['type'] != 'summary'):
        issues.append('the final scene must be a concise summary; preserve the complete instructional sequence')
    if total_estimated < target_seconds * 0.70:
        issues.append(f"estimated visual time is only {total_estimated:.0f}s for a {target_seconds:.0f}s target")
    if total_estimated > target_seconds * 1.35:
        issues.append(f"estimated visual time is {total_estimated:.0f}s, too long for a {target_seconds:.0f}s target")
    minimum_words = max(120, math.ceil(target_minutes * 130 * 0.85))
    if words < minimum_words:
        issues.append(f"narration has {words} words; at least {minimum_words} are needed for sufficient depth")
    if any(not scene.get("narration_text", "").strip() for scene in scenes):
        issues.append("every scene must contain narration_text")
    for index, scene in enumerate(scenes, start=1):
        if scene.get("type") == "process" and any(len(step) > 45 for step in scene.get("steps", [])):
            issues.append(f"scene {index} has process text that is too dense to read")
        if scene.get("type") == "worked_example" and len(scene.get("steps", [])) > 4:
            issues.append(f"scene {index} has too many worked-example steps")
    if len(scenes) < max(3, math.ceil(target_seconds / 45)):
        issues.append("the lesson needs more scenes to support the requested duration")
    if non_static < max(2, math.ceil(len(scenes) * 0.4)):
        issues.append("too many scenes are static text slides; add explanatory visual scenes")
    if mathematical:
        if not any(visual in {"equation", "worked_example"} for visual in visual_types):
            issues.append("mathematical lessons need an equation or worked-example scene")
        if not any(visual == "graph" for visual in visual_types):
            issues.append("mathematical lessons need a graph or other meaningful visual demonstration")

    if issues:
        raise LessonQualityError("Lesson quality validation failed: " + "; ".join(issues), issues=issues)


def validate_audio_timing(durations: list[float], target_minutes: float) -> None:
    """Check measured narration time before starting an expensive render."""
    total = sum(durations)
    target = target_minutes * 60
    if total < target * 0.70:
        raise LessonQualityError(
            f"Measured narration is {total:.0f}s for a {target:.0f}s target; "
            "expand the lesson or use more detailed narration."
        )
    if total > target * 1.40:
        raise LessonQualityError(
            f"Measured narration is {total:.0f}s for a {target:.0f}s target; "
            "reduce repetition or increase speech speed."
        )
