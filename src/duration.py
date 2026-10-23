"""Measured-audio scene splitting and target-duration validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .tts import AudioSegment, KokoroTTS, NarrationTiming
from .video.timeline import bind_audio_timeline, estimated_timeline, process_timeline
from .video.sequence import identify_lesson, validate_sequence

MAX_SCENE_AUDIO_SECONDS = 44.5


class DurationConstraintError(ValueError):
    """Raised when measured audio cannot fit the scene or target limits."""


@dataclass(frozen=True)
class PreparedAudioLesson:
    lesson: dict
    audio_segments: list[AudioSegment]
    total_audio_duration: float
    split_count: int


def prepare_audio_lesson(lesson: dict, audio_segments: list[AudioSegment], tts: KokoroTTS,
                         *, reporter: Any | None = None) -> PreparedAudioLesson:
    if len(lesson["scenes"]) != len(audio_segments):
        raise DurationConstraintError("Narration and lesson scene counts do not match.")
    lesson = identify_lesson(lesson)
    for scene in lesson['scenes']:
        if not scene.get('visual_timeline'):
            scene['visual_timeline'] = process_timeline(scene) if scene['type'] == 'process' else estimated_timeline(scene)
    lesson = identify_lesson(lesson)
    prepared_scenes: list[dict] = []
    prepared_audio: list[AudioSegment] = []
    split_count = 0
    for index, (scene, audio) in enumerate(zip(lesson["scenes"], audio_segments), start=1):
        _validate_audio_coverage(scene, audio)
        if audio.duration <= MAX_SCENE_AUDIO_SECONDS:
            prepared_scenes.append(bind_audio_timeline(_with_audio_duration(scene, audio.duration), audio))
            prepared_audio.append(audio)
            continue
        chunks = (_split_measured_scene(scene, audio) if audio.timings else
                  _split_overlong_scene(scene, audio, tts, index))
        if not audio.timings:
            _align_legacy_chunks(scene, chunks)
        split_count += len(chunks) - 1
        if reporter:
            reporter.narration_split(index, scene.get("title", f"Scene {index}"), len(chunks), audio.duration)
        for part, (chunk_scene, chunk_audio) in enumerate(chunks):
            chunk_scene['scene_id'] = f"{scene['scene_id']}.part{part+1:03d}"
            prepared_scenes.append(bind_audio_timeline(chunk_scene, chunk_audio))
            prepared_audio.append(chunk_audio)
    result = dict(lesson)
    result["scenes"] = prepared_scenes
    validate_sequence(result, lesson)
    return PreparedAudioLesson(
        result, prepared_audio, sum(segment.duration for segment in prepared_audio), split_count
    )


def _validate_audio_coverage(scene: dict, audio: AudioSegment) -> None:
    if not audio.timings:
        return
    end = 0.0
    for timing in audio.timings:
        if abs(timing.start-end) > 0.02:
            raise DurationConstraintError('Narration timings have missing or reordered audio; regenerate the narration cache.')
        end = timing.start + timing.duration
    if abs(end-audio.duration) > 0.02:
        raise DurationConstraintError('Narration timings do not cover the complete audio. Refusing to cut essential narration.')
    if ' '.join(t.text for t in audio.timings).split() != scene.get('narration_text', '').split():
        raise DurationConstraintError('Narration timings differ from the lesson text. Regenerate the narration cache.')


def validate_measured_duration(total_seconds: float, target_minutes: float,
                               tolerance: float = 0.15) -> None:
    if not 0.05 <= tolerance <= 0.30:
        raise ValueError("Duration tolerance must be between 0.05 and 0.30.")
    target = target_minutes * 60
    lower, upper = target * (1 - tolerance), target * (1 + tolerance)
    if not lower <= total_seconds <= upper:
        raise DurationConstraintError(
            f"Measured narration is {total_seconds:.1f}s; target is {target:.1f}s "
            f"(allowed range {lower:.1f}-{upper:.1f}s, ±{tolerance:.0%})."
        )


def _with_audio_duration(scene: dict, duration: float) -> dict:
    updated = dict(scene)
    updated["duration"] = max(3.0, min(45.0, duration + 0.35))
    return updated


def _split_measured_scene(scene: dict, audio: AudioSegment) -> list[tuple[dict, AudioSegment]]:
    """Cut at measured phrase boundaries, preserving exact audio and visual cues."""
    import hashlib
    import soundfile
    groups, current, length = [], [], 0.0
    for timing in audio.timings:
        if timing.duration > MAX_SCENE_AUDIO_SECONDS:
            raise DurationConstraintError("A narration phrase exceeds the scene limit. Regenerate shorter TTS phrases.")
        if current and length + timing.duration > MAX_SCENE_AUDIO_SECONDS:
            groups.append(current)
            current, length = [], 0.0
        current.append(timing)
        length += timing.duration
    if current:
        groups.append(current)
    data, rate = soundfile.read(str(audio.path), dtype="float32")
    result = []
    for index, group in enumerate(groups):
        start = round(group[0].start * rate)
        end = round((group[-1].start + group[-1].duration) * rate)
        digest = hashlib.sha256(f"{audio.path}:{start}:{end}".encode()).hexdigest()[:24]
        path = audio.path.with_name(f"split_{digest}.wav")
        if not path.exists():
            soundfile.write(str(path), data[start:end], rate)
        timings = tuple(NarrationTiming(item.text, item.start - group[0].start,
                                       item.duration, item.cue_index) for item in group)
        duration = (end - start) / rate
        segment = AudioSegment(path, duration, timings)
        updated = _with_audio_duration(dict(scene, narration_text=" ".join(item.text for item in group)), duration)
        _adapt_visual_segment(updated, index, len(groups))
        updated['animation_params']['timeline_offset'] = group[0].start
        result.append((updated, segment))
    return result


def _split_overlong_scene(scene: dict, audio: AudioSegment, tts: KokoroTTS,
                          scene_index: int) -> list[tuple[dict, AudioSegment]]:
    text = scene.get("narration_text", "").strip()
    if not text:
        raise DurationConstraintError(f"Scene {scene_index} exceeds 45 seconds but has no narration text to split.")
    part_count = max(2, math.ceil(audio.duration / MAX_SCENE_AUDIO_SECONDS))
    texts = _partition_text(text, part_count)
    chunks: list[tuple[dict, AudioSegment]] = []
    for part_index, part_text in enumerate(texts):
        segment = tts.synthesize(part_text, scene_index * 100 + part_index)
        if segment.duration > MAX_SCENE_AUDIO_SECONDS:
            nested = _split_overlong_scene(
                dict(scene, narration_text=part_text), segment, tts, scene_index * 100 + part_index
            )
            chunks.extend(nested)
            continue
        updated = dict(scene)
        updated["narration_text"] = part_text
        updated = _with_audio_duration(updated, segment.duration)
        _adapt_visual_segment(updated, part_index, len(texts))
        chunks.append((updated, segment))
    if not chunks:
        raise DurationConstraintError(f"Could not split narration for scene {scene_index}.")
    return chunks


def _partition_text(text: str, count: int) -> list[str]:
    units = [unit.strip() for unit in re.split(r"(?<=[.!?;])\s+", text) if unit.strip()]
    if len(units) < count:
        words = text.split()
        if len(words) < 2:
            raise DurationConstraintError("A single-word narration cannot be split into duration-safe scenes.")
        units = [" ".join(words[i:i + max(1, math.ceil(len(words) / count))])
                 for i in range(0, len(words), max(1, math.ceil(len(words) / count)))]
    count = min(count, len(units))
    if count <= 1:
        return [text]
    total = sum(len(unit) for unit in units)
    result: list[str] = []
    current: list[str] = []
    current_length = 0
    for index, unit in enumerate(units):
        remaining_units = len(units) - index
        remaining_groups = count - len(result)
        if current and remaining_units <= remaining_groups:
            result.append(" ".join(current))
            current, current_length = [], 0
        current.append(unit)
        current_length += len(unit) + 1
        target = total / count
        if current_length >= target and len(result) < count - 1:
            result.append(" ".join(current))
            current, current_length = [], 0
    if current:
        result.append(" ".join(current))
    return result


def _adapt_visual_segment(scene: dict, index: int, total: int) -> None:
    params = dict(scene.get("animation_params", {}))
    params["segment_index"] = index
    params["segment_count"] = total
    scene["animation_params"] = params


def _align_legacy_chunks(source: dict, chunks: list) -> None:
    """Keep global process/cue positions even for integrations without TTS timings."""
    from copy import deepcopy
    from .video.timeline import narration_units
    sources = source['visual_timeline']
    boundaries, position = [], 0
    for cue in sources:
        end = position + len(cue.get('narration_text', '').split())
        boundaries.append((position, end))
        position = end
    consumed, offset = 0, 0.0
    for index, (scene, audio) in enumerate(chunks):
        cues, words = [], scene['narration_text'].split()
        for source_cue, (start, end) in zip(sources, boundaries):
            left, right = max(start, consumed), min(end, consumed+len(words))
            if left >= right:
                continue
            for unit in narration_units(' '.join(words[left-consumed:right-consumed])):
                cue = deepcopy(source_cue)
                cue['narration_text'] = unit
                cues.append(cue)
        consumed += len(words)
        scene['visual_timeline'] = cues
        _adapt_visual_segment(scene, index, len(chunks))
        scene['animation_params']['timeline_offset'] = offset
        offset += audio.duration
