"""Backward-compatible Phase 1 validation entry point."""

from typing import Any

from src.validator import plan_to_dict


def validate_lesson(data: Any) -> dict:
    return plan_to_dict(data)
