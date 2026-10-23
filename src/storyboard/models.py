"""Data-only scene graph in normalized, top-left-origin 16:9 coordinates."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from src.schemas import Scene as LessonScene


class Data(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Point(Data):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Theme(Data):
    name: str = 'daylight'
    background: str = Field(default='#EAF7FF', pattern=r'^#[0-9A-Fa-f]{6}$')
    sky_top: str = Field(default='#D1EEFF', pattern=r'^#[0-9A-Fa-f]{6}$')
    foreground: str = Field(default='#16384C', pattern=r'^#[0-9A-Fa-f]{6}$')
    accent: str = Field(default='#087DA5', pattern=r'^#[0-9A-Fa-f]{6}$')
    panel: str = Field(default='#FFFFFF', pattern=r'^#[0-9A-Fa-f]{6}$')
    ground: str = Field(default='#CDE6BF', pattern=r'^#[0-9A-Fa-f]{6}$')


class Evidence(Data):
    source_id: str = Field(min_length=1, max_length=120)
    uri: str = Field(min_length=1, max_length=1000)
    excerpt: str = Field(default='', max_length=2000)
    locator: str = Field(default='', max_length=160)


class NarrationCue(Data):
    id: str = Field(min_length=1, max_length=180)
    text: str = Field(min_length=1, max_length=4000)
    start: float = Field(ge=0)
    duration: float = Field(gt=0)
    concepts: list[str] = Field(min_length=1)
    source_scene_id: str = ''
    source_event_id: str = ''
    step_index: int | None = Field(default=None,ge=0)
    evidence: list[Evidence] = Field(default_factory=list)


class VisualObject(Data):
    id: str = Field(min_length=1, max_length=90, pattern=r'^[A-Za-z0-9_-]+$')
    kind: Literal['asset', 'circle', 'rectangle', 'label', 'arrow', 'polyline']
    role: Literal['illustration', 'label', 'caption', 'decoration'] = 'illustration'
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    z: int = Field(default=0, ge=0, le=100)
    color: str = Field(default='#329CC8', pattern=r'^#[0-9A-Fa-f]{6}$')
    opacity: float = Field(default=1, ge=0, le=1)
    asset: str = ''
    text: str = Field(default='', max_length=1000)
    font_size: int = Field(default=32, ge=28, le=64)
    concepts: list[str] = Field(default_factory=list)
    points: list[Point] = Field(default_factory=list,max_length=256)
    visible_from: float = Field(default=0,ge=0)
    visible_until: float | None = Field(default=None,gt=0)

    @model_validator(mode='after')
    def valid_payload(self):
        if self.kind == 'asset':
            from .assets import asset_path
            asset_path(self.asset)
        elif self.asset:
            raise ValueError('Only asset objects can refer to an asset ID.')
        if self.kind == 'label' and not self.text.strip():
            raise ValueError('Labels must contain readable text.')
        if self.kind=='polyline' and len(self.points)<2:
            raise ValueError('Polyline diagrams need at least two ordered points.')
        if self.visible_until is not None and self.visible_until<=self.visible_from:
            raise ValueError('Object visibility must have a positive chronological interval.')
        return self


class Motion(Data):
    id: str = Field(min_length=1, max_length=120)
    object_id: str
    action: Literal['move', 'scale', 'opacity', 'rotate']
    start: float = Field(ge=0)
    duration: float = Field(gt=0)
    path: list[Point] = Field(default_factory=list, max_length=12)
    from_value: float = 1
    to_value: float = 1
    loop: bool = False
    easing: Literal['linear', 'smooth'] = 'smooth'
    narration_ids: list[str] = Field(min_length=1)
    concepts: list[str] = Field(min_length=1)

    @model_validator(mode='after')
    def valid_motion(self):
        if self.action == 'move' and len(self.path) < 2:
            raise ValueError('Moving objects need at least two path points.')
        if self.action == 'opacity' and not (0 <= self.from_value <= 1 and 0 <= self.to_value <= 1):
            raise ValueError('Opacity must be between zero and one.')
        if self.action == 'scale' and not (0 < self.from_value <= 3 and 0 < self.to_value <= 3):
            raise ValueError('Scale must be positive and at most three.')
        return self


class Transition(Data):
    kind: Literal['crossfade', 'cut'] = 'crossfade'
    duration: float = Field(default=0.35, ge=0, le=1)

    @model_validator(mode='after')
    def crossfade_duration(self):
        if self.kind == 'crossfade' and self.duration <= 0:
            raise ValueError('Crossfade duration must be positive; use a cut for an instant transition.')
        return self


class StoryboardScene(Data):
    id: str = Field(min_length=1, max_length=90,pattern=r'^[A-Za-z0-9_.:-]+$')
    title: str = Field(min_length=1, max_length=90)
    objective: str = Field(min_length=1, max_length=300)
    duration: float = Field(gt=0, le=300)
    concepts: list[str] = Field(min_length=1)
    layout: Literal['landscape', 'horizontal_flow', 'vertical_flow', 'comparison', 'math', 'concept_map', 'signal', 'network', 'cycle']
    template: str = Field(default='',max_length=90)
    selection_reason: str = Field(default='',max_length=1000)
    selection_assets: list[str] = Field(default_factory=list)
    purpose: Literal['introduce','explain','compare','summarize'] = 'explain'
    source_scene_id: str = ''
    native_scene: LessonScene | None = None
    background: Literal['sky', 'solid'] = 'sky'
    layout_justification: str = ''
    repetition_justification: str = ''
    narration: list[NarrationCue] = Field(min_length=1)
    objects: list[VisualObject] = Field(default_factory=list,max_length=120)
    motions: list[Motion] = Field(default_factory=list,max_length=240)
    transition: Transition = Field(default_factory=Transition)

    @model_validator(mode='after')
    def references_and_timing(self):
        if self.id in {'.','..'}:
            raise ValueError('Scene IDs cannot be directory traversal components.')
        if self.native_scene is None and (not self.objects or not self.motions):
            raise ValueError('Illustrated scenes need explanatory objects and motion, or a validated native Manim scene.')
        if self.native_scene is not None and self.layout!='math':
            raise ValueError('Native mathematical scenes must use the math layout.')
        objects = {o.id for o in self.objects}
        cues = {c.id for c in self.narration}
        if len(objects) != len(self.objects) or len(cues) != len(self.narration):
            raise ValueError('Object and narration IDs must be unique within each scene.')
        if len({m.id for m in self.motions}) != len(self.motions):
            raise ValueError('Motion IDs must be unique.')
        end = 0.0
        for cue in self.narration:
            if abs(cue.start-end) > 0.04:
                raise ValueError('Narration must remain chronological and cover the complete scene.')
            end = cue.start+cue.duration
        if abs(end-self.duration) > 0.04:
            raise ValueError('Scene duration must match its measured narration.')
        for motion in self.motions:
            if motion.object_id not in objects or not set(motion.narration_ids) <= cues:
                raise ValueError('Every motion must refer to known objects and narration IDs.')
            if motion.start+motion.duration > self.duration+0.04:
                raise ValueError('Motion extends past the scene narration.')
        for obj in self.objects:
            if obj.visible_from>=self.duration or (obj.visible_until is not None and obj.visible_until>self.duration+.04):
                raise ValueError('Object visibility extends outside the scene.')
        if self.transition.duration > self.duration:
            raise ValueError('Transition cannot exceed the scene duration.')
        return self


class Storyboard(Data):
    schema_version: Literal[1] = 1
    title: str
    topic: str
    width: int = Field(default=1280, ge=640, le=3840)
    height: int = Field(default=720, ge=360, le=2160)
    fps: Literal[30] = 30
    theme: Theme = Field(default_factory=Theme)
    required_concepts: list[str] = Field(min_length=1)
    scenes: list[StoryboardScene] = Field(min_length=1, max_length=60)
    provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique_scenes(self):
        if len({s.id for s in self.scenes}) != len(self.scenes):
            raise ValueError('Storyboard scene IDs must be unique.')
        if abs(self.width/self.height-16/9) > 0.01:
            raise ValueError('Storyboard canvas must use a 16:9 aspect ratio.')
        return self

    @property
    def duration(self):
        return sum(s.duration for s in self.scenes)
