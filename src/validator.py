"""Validation boundary between generated data and the renderer."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .schemas import LessonOutline, LessonPlan


class LessonValidationError(ValueError):
    """Raised when a lesson does not satisfy the renderer contract."""


def validate_plan(data: Any) -> LessonPlan:
    try:
        return LessonPlan.model_validate(data)
    except ValidationError as exc:
        raise LessonValidationError(f"Invalid lesson plan: {exc}") from exc


def validate_outline(data: Any) -> LessonOutline:
    try:
        return LessonOutline.model_validate(data)
    except ValidationError as exc:
        raise LessonValidationError(f"Invalid lesson outline: {exc}") from exc


def plan_to_dict(data: Any) -> dict:
    # Excluding schema defaults keeps the Phase 1 JSON API backwards compatible
    # (for example, an omitted title-scene subtitle stays omitted).
    return validate_plan(data).model_dump(mode="json", exclude_defaults=True)

