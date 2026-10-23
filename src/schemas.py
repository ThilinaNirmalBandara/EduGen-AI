"""Pydantic schemas for the safe, data-only lesson contract."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutlineSection(_Schema):
    section_id: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=90)
    learning_objective: str = Field(min_length=1, max_length=240)
    key_concepts: list[str] = Field(min_length=1, max_length=6)
    visual_type: Literal["concepts", "process", "equation", "graph", "worked_example", "comparison"]


class LessonOutline(_Schema):
    title: str = Field(min_length=1, max_length=120)
    audience: str = Field(min_length=1, max_length=120)
    learning_goals: list[str] = Field(min_length=2, max_length=6)
    sections: list[OutlineSection] = Field(min_length=3, max_length=12)


class VisualEvent(_Schema):
    event_id: str = Field(default="", max_length=180)
    source_event_id: str = Field(default="", max_length=180)
    event_order: int = Field(default=0, ge=0)
    narration_text: str = Field(default="", max_length=4000)
    narration_segment: int = Field(default=0, ge=0)
    action: Literal["reveal", "highlight", "gradient", "update", "loss", "equation", "compare"] = "reveal"
    start_time: float = Field(default=0, ge=0)
    animation_duration: float = Field(default=1, gt=0, le=45)
    highlight_target: str = Field(default="", max_length=90)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)


class SceneMetadata(_Schema):
    scene_id: str = Field(default="", max_length=90)
    parent_scene_id: str = Field(default="", max_length=90)
    visual_template: Literal["", "concept", "tangent", "learning_rates", "derivation", "numerical", "descent", "convergence", "process", "summary"] = ""
    learning_objective: str = Field(default="", max_length=240)
    visual_type: Literal["", "title", "concepts", "process", "equation", "graph", "worked_example", "comparison"] = ""
    animation_params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    important_concepts: list[str] = Field(default_factory=list, max_length=6)
    visual_timeline: list[VisualEvent] = Field(default_factory=list, max_length=100)


class TitleScene(SceneMetadata):
    type: Literal["title"]
    title: str = Field(min_length=1, max_length=90)
    subtitle: str = Field(default="", max_length=160)
    duration: float = Field(ge=3, le=45)
    narration_text: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class BulletScene(SceneMetadata):
    type: Literal["bullets", "summary"]
    title: str = Field(min_length=1, max_length=90)
    bullets: list[str] = Field(min_length=1, max_length=5)
    duration: float = Field(ge=3, le=45)
    narration_text: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("bullets")
    @classmethod
    def validate_bullets(cls, value: list[str]) -> list[str]:
        if any(not 1 <= len(item.strip()) <= 150 for item in value):
            raise ValueError("each bullet must be 1–150 characters")
        return value


class ProcessScene(SceneMetadata):
    type: Literal["process"]
    title: str = Field(min_length=1, max_length=90)
    steps: list[str] = Field(min_length=2, max_length=4)
    duration: float = Field(ge=3, le=45)
    narration_text: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, value: list[str]) -> list[str]:
        if any(not 1 <= len(step.strip()) <= 45 for step in value):
            raise ValueError("each step must be 1–45 characters")
        return value


class EquationScene(SceneMetadata):
    type: Literal["equation"]
    title: str = Field(min_length=1, max_length=90)
    equation: str = Field(min_length=1, max_length=180)
    duration: float = Field(ge=3, le=45)
    narration_text: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class GraphScene(SceneMetadata):
    type: Literal["graph"]
    title: str = Field(min_length=1, max_length=90)
    graph_type: Literal["gradient_descent", "parabola"] = "gradient_descent"
    equation: str = Field(default="y = x²", max_length=180)
    duration: float = Field(ge=3, le=45)
    narration_text: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class WorkedExampleScene(SceneMetadata):
    type: Literal["worked_example"]
    title: str = Field(min_length=1, max_length=90)
    equation: str = Field(min_length=1, max_length=180)
    steps: list[str] = Field(min_length=2, max_length=4)
    duration: float = Field(ge=3, le=45)
    narration_text: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, value: list[str]) -> list[str]:
        if any(not 1 <= len(step.strip()) <= 80 for step in value):
            raise ValueError("each worked-example step must be 1–80 characters")
        return value


class ComparisonScene(SceneMetadata):
    type: Literal["comparison"]
    title: str = Field(min_length=1, max_length=90)
    left: str = Field(min_length=1, max_length=100)
    right: str = Field(min_length=1, max_length=100)
    relationship: str = Field(min_length=1, max_length=140)
    duration: float = Field(ge=3, le=45)
    narration_text: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


Scene = Annotated[
    Union[TitleScene, BulletScene, ProcessScene, EquationScene, GraphScene, WorkedExampleScene, ComparisonScene],
    Field(discriminator="type"),
]


class LessonPlan(_Schema):
    title: str = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=1, max_length=20)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class SceneNarration(_Schema):
    scene_index: int = Field(ge=1, le=20, description="One-based scene index in the supplied lesson")
    narration_text: str = Field(min_length=1, max_length=4000)


class NarrationRevision(_Schema):
    scenes: list[SceneNarration] = Field(min_length=1, max_length=20)


def narration_word_pattern(minimum: int, maximum: int) -> str:
    """A bounded, JSON-safe spoken-word budget for narration-only repairs.

    Mathematical commands belong in equation fields, not spoken narration.
    Use ordinary space-separated prose with no raw LaTeX or embedded quotes.
    """
    if not 1 <= minimum <= maximum <= 2000:
        raise ValueError('Narration word bounds must satisfy 1 <= minimum <= maximum <= 2000.')
    # A negated class also admits Chinese and escaped Unicode. Use explicit
    # English prose characters so the decoder cannot keep substituting Chinese
    # synonyms. Bound word length as well to respect narration's 4000-char cap.
    length = min(64, 4000 // maximum - 1)
    word = rf"[A-Za-z0-9][A-Za-z0-9.,;:!?()'/%+=-]{{0,{length-1}}}"
    return f'^{word}( {word}){{{minimum-1},{maximum-1}}}$'


ENGLISH_TEXT_CHARACTERS = r"[A-Za-z0-9 .,;:!?()'/%+=_{}<>|&*-]"


def english_generation_schema(schema: dict) -> dict:
    """Generation-only patterns; legacy Unicode JSON remains valid in Pydantic.

    Include string lengths in the pattern: some grammar backends prioritize
    `pattern` over minLength/maxLength instead of intersecting them.
    """
    result = deepcopy(schema)
    def constrain(node):
        if isinstance(node, dict):
            if node.get('type') == 'string' and 'const' not in node and 'enum' not in node:
                minimum = node.get('minLength', 0)
                maximum = node.get('maxLength')
                repeat = f'{{{minimum},{maximum}}}' if maximum is not None else ('+' if minimum else '*')
                node['pattern'] = f'^{ENGLISH_TEXT_CHARACTERS}{repeat}$'
            for key, value in node.items():
                # Math has its own LaTeX allowlist and Unicode normalization.
                # Do not constrain its JSON-escaped backslashes as raw prose.
                if key != 'equation':
                    constrain(value)
        elif isinstance(node, list):
            for value in node:
                constrain(value)
    constrain(result)
    return result


def lesson_generation_schema(scene_types: list[str] | None = None,
                             target_seconds: float | None = None) -> dict:
    """Require spoken content for generation while keeping legacy JSON optional.

    Pydantic field validators are not exported as JSON Schema constraints. Mirror
    their display-string bounds here so Ollama can constrain its output as well.
    The complete result still goes through the original Python validators.
    """
    schema = english_generation_schema(LessonPlan.model_json_schema())
    for name, definition in schema["$defs"].items():
        properties = definition.get("properties", {})
        if "narration_text" not in properties or 'duration' not in properties:
            continue
        # Renderer-owned timing data must never participate in model decoding.
        properties.pop('visual_timeline', None)
        for runtime_field in ('scene_id', 'parent_scene_id', 'visual_template'):
            properties.pop(runtime_field, None)
        if 'animation_params' in properties:
            properties['animation_params'] = {
                'type': 'object', 'additionalProperties': False,
                'properties': {
                    'initial_x': {'type': 'number', 'minimum': -3, 'maximum': 3},
                    'learning_rate': {'type': 'number', 'exclusiveMinimum': 0, 'exclusiveMaximum': 1},
                    'iterations': {'type': 'integer', 'minimum': 1, 'maximum': 12},
                    'start_x': {'type': 'number', 'minimum': -3, 'maximum': 3},
                    'end_x': {'type': 'number', 'minimum': -3, 'maximum': 3},
                },
            }
        definition.setdefault("required", []).append("narration_text")
        properties["narration_text"].pop("default", None)
        properties["narration_text"]["minLength"] = 1
        properties['narration_text']['pattern'] = f'^{ENGLISH_TEXT_CHARACTERS}{{1,4000}}$'
        # JSON grammar numeric float bounds are not universally enforced.
        # An enum of legal second values excludes millisecond-like 12000 values.
        properties['duration']['enum'] = list(range(3, 46))
        if name == "ProcessScene":
            properties["steps"]["items"].update(minLength=1, maxLength=45)
            properties['steps']['items']['pattern'] = f'^{ENGLISH_TEXT_CHARACTERS}{{1,45}}$'
        elif name == "WorkedExampleScene":
            properties["steps"]["items"].update(minLength=1, maxLength=80)
            properties['steps']['items']['pattern'] = f'^{ENGLISH_TEXT_CHARACTERS}{{1,80}}$'
        elif name == "BulletScene":
            properties["bullets"]["items"].update(minLength=1, maxLength=150)
            properties['bullets']['items']['pattern'] = f'^{ENGLISH_TEXT_CHARACTERS}{{1,150}}$'
    schema['$defs'].pop('VisualEvent', None)
    if scene_types:
        definitions = {
            "title": "TitleScene", "bullets": "BulletScene", "summary": "BulletScene",
            "process": "ProcessScene", "equation": "EquationScene", "graph": "GraphScene",
            "worked_example": "WorkedExampleScene", "comparison": "ComparisonScene",
        }
        scenes = schema["properties"]["scenes"]
        # A tuple schema makes the planned visual types mandatory by position.
        # Leave `items` absent: llama.cpp chooses it before `prefixItems`.
        scenes.pop("items")
        scenes["prefixItems"] = []
        scenes["minItems"] = scenes["maxItems"] = len(scene_types)
        for scene_type in scene_types:
            item = deepcopy(schema["$defs"][definitions[scene_type]])
            item["properties"]["type"] = {"type": "string", "const": scene_type}
            if target_seconds:
                item["properties"]["duration"]["minimum"] = max(
                    3, min(45, target_seconds / len(scene_types) * 0.85)
                )
                lower = math.ceil(item['properties']['duration']['minimum'])
                item['properties']['duration']['enum'] = list(range(lower, 46))
                minutes = target_seconds / 60
                minimum_words = max(math.ceil(120 / len(scene_types)), math.floor(minutes * 130 / len(scene_types)))
                maximum_words = max(minimum_words, math.ceil(minutes * 150 / len(scene_types)))
                item['properties']['narration_text']['pattern'] = narration_word_pattern(minimum_words, maximum_words)
                item['properties']['narration_text']['description'] = (
                    f'English spoken narration: {minimum_words}-{maximum_words} words, separated by single spaces. '
                    'Use ASCII punctuation and spoken words for mathematical symbols. Explain this scene in depth, not filler.'
                )
            scenes["prefixItems"].append(item)
    return schema

