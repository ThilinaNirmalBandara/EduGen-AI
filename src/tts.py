"""Modular Kokoro narration generation with content-addressed caching."""

from __future__ import annotations

import hashlib
import json
import wave
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TTSDependencyError(RuntimeError):
    """Raised when Kokoro or its audio writer is unavailable."""


class TTSError(RuntimeError):
    """Raised when narration synthesis fails."""


@dataclass(frozen=True)
class NarrationTiming:
    text: str
    start: float
    duration: float
    cue_index: int = 0

    def __post_init__(self):
        if not math.isfinite(self.start) or not math.isfinite(self.duration) or self.start < 0 or self.duration <= 0 or self.cue_index < 0:
            raise ValueError("Narration timing must have finite, positive duration and a nonnegative start/cue index.")


@dataclass(frozen=True)
class AudioSegment:
    path: Path
    duration: float
    timings: tuple[NarrationTiming, ...] = ()

    def __post_init__(self):
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise TTSError(f"Narration audio is empty or has invalid duration: {self.path}")


class KokoroTTS:
    def __init__(self, voice: str = "af_heart", speed: float = 1.0,
                 cache_dir: Path = Path("outputs/narration"), reporter: Any | None = None,
                 force: bool = False) -> None:
        if speed <= 0:
            raise ValueError("Speech speed must be greater than zero.")
        self.voice = voice
        self.speed = speed
        self.cache_dir = Path(cache_dir)
        self.reporter = reporter
        self.force = force
        self._pipeline = None

    def synthesize_plan(self, lesson: dict) -> list[AudioSegment]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        scenes = lesson["scenes"]
        if self.reporter:
            self.reporter.tts_started(len(scenes), self.voice, self.speed)
        segments = []
        for index, scene in enumerate(scenes, start=1):
            text = narration_for_scene(scene)
            if self.reporter:
                self.reporter.tts_scene_started(index, len(scenes), scene.get("title", "Scene"))
            segment = self.synthesize_timed(text, index, cues=scene.get("visual_timeline"))
            segments.append(segment)
            if self.reporter:
                self.reporter.tts_scene_completed(index, len(scenes), segment.duration)
        if self.reporter:
            self.reporter.tts_finished()
        return segments

    def synthesize_timed(self, text: str, index: int, *, cues: list[dict] | None = None) -> AudioSegment:
        """Synthesize short phrases and assemble audio with exact sample boundaries."""
        from .video.timeline import narration_units
        if cues:
            cue_text = " ".join(cue.get("narration_text", "") for cue in cues)
            if " ".join(cue_text.split()) != " ".join(text.split()):
                raise TTSError(f"Scene {index} narration and visual cue text disagree. Rebuild its visual timeline.")
        source = [(cue["narration_text"], cue_index) for cue_index, cue in enumerate(cues)] if cues else [(text, 0)]
        units = [(unit, cue_index) for value, cue_index in source for unit in narration_units(value)]
        if not units:
            raise TTSError(f"Scene {index} has no narration text.")
        digest = hashlib.sha256(json.dumps({"version": 1, "units": units, "voice": self.voice,
                                           "speed": self.speed}, sort_keys=True).encode()).hexdigest()[:24]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"timed_{digest}.wav"
        manifest = path.with_suffix(".json")
        if path.exists() and manifest.exists() and not self.force:
            try:
                timings = tuple(NarrationTiming(**item) for item in json.loads(manifest.read_text()))
                duration = audio_duration(path)
                if timings and abs(sum(item.duration for item in timings) - duration) < 0.05:
                    return AudioSegment(path, duration, timings)
            except (OSError, TypeError, ValueError):
                pass
        pieces = []
        def add_unit(unit: str, cue_index: int) -> None:
            segment = self.synthesize(unit, index)
            if segment.duration > 8:
                words = unit.split()
                if len(words) < 2:
                    raise TTSError(f"A spoken word in scene {index} exceeds 8s. Increase speech speed or correct its pronunciation.")
                middle = len(words) // 2
                add_unit(" ".join(words[:middle]), cue_index)
                add_unit(" ".join(words[middle:]), cue_index)
            else:
                pieces.append((unit, cue_index, segment))
        for unit, cue_index in units:
            add_unit(unit, cue_index)
        if len(pieces) == 1:
            unit, cue_index, segment = pieces[0]
            return AudioSegment(segment.path, segment.duration,
                                (NarrationTiming(unit, 0, segment.duration, cue_index),))
        import numpy as np
        import soundfile
        arrays, timings, start, rate = [], [], 0.0, None
        for unit, cue_index, segment in pieces:
            data, current_rate = soundfile.read(str(segment.path), dtype="float32")
            if rate is not None and current_rate != rate:
                raise TTSError("Narration chunks have inconsistent sample rates.")
            rate = current_rate
            duration = len(data) / rate
            timings.append(NarrationTiming(unit, start, duration, cue_index))
            arrays.append(data)
            start += duration
        soundfile.write(str(path), np.concatenate(arrays), rate)
        from dataclasses import asdict
        manifest.write_text(json.dumps([asdict(item) for item in timings]), encoding="utf-8")
        return AudioSegment(path, start, tuple(timings))

    def synthesize(self, text: str, index: int) -> AudioSegment:
        if not text.strip():
            raise TTSError(f"Scene {index} has no narration text.")
        digest = hashlib.sha256(
            json.dumps({"text": text, "voice": self.voice, "speed": self.speed}, sort_keys=True).encode()
        ).hexdigest()[:20]
        path = self.cache_dir / f"narration_{digest}.wav"
        if path.exists() and not self.force:
            return AudioSegment(path, audio_duration(path))
        pipeline, soundfile = self._load_dependencies()
        try:
            generator = pipeline(text, voice=self.voice, speed=self.speed)
            chunks = []
            for _, _, chunk in generator:
                chunks.append(chunk)
            if not chunks:
                raise TTSError(f"Kokoro returned no audio for scene {index}.")
            if len(chunks) == 1:
                audio = chunks[0]
            else:
                import numpy as np
                audio = np.concatenate(chunks)
            sample_rate = getattr(pipeline, "sample_rate", 24000)
            soundfile.write(str(path), audio, sample_rate)
        except TTSError:
            raise
        except Exception as exc:
            raise TTSError(f"Kokoro failed for scene {index}: {exc}") from exc
        return AudioSegment(path, audio_duration(path))

    def _load_dependencies(self):
        try:
            from kokoro import KPipeline
            import soundfile
        except ImportError as exc:
            raise TTSDependencyError(
                "Kokoro narration dependencies are missing. Install them with: "
                "python -m pip install -r requirements.txt"
            ) from exc
        if self._pipeline is None:
            try:
                self._pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
            except Exception as exc:
                raise TTSDependencyError(f"Could not initialize Kokoro: {exc}") from exc
        return self._pipeline, soundfile


def narration_for_scene(scene: dict) -> str:
    """Use explicit narration, with a safe fallback for legacy JSON lessons."""
    if scene.get("narration_text", "").strip():
        return scene["narration_text"]
    kind = scene["type"]
    if kind == "title":
        return f"In this lesson, we will learn about {scene['title']}. {scene.get('subtitle', '')}".strip()
    if kind in {"bullets", "summary"}:
        return f"{scene['title']}. " + " ".join(scene["bullets"])
    if kind == "process":
        return f"{scene['title']}. " + " Then, ".join(scene["steps"])
    if kind == "comparison":
        return f"{scene['title']}. On one side is {scene['left']}; on the other is {scene['right']}. {scene['relationship']}"
    return f"{scene['title']}. The key expression is {scene.get('equation', '')}."


def audio_duration(path: Path) -> float:
    try:
        import soundfile
        return float(soundfile.info(str(path)).duration)
    except ImportError:
        with wave.open(str(path), "rb") as audio:
            frames = audio.getnframes()
            rate = audio.getframerate()
        return frames / rate
