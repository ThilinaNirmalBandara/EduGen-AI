"""A fair, cache-only comparison excerpt; no Ollama imports or TTS generation."""
import hashlib
import json
from pathlib import Path
import numpy as np
import soundfile
from src.tts import AudioSegment, NarrationTiming
from src.schemas import VisualEvent
from .models import NarrationCue, Storyboard
from .themes import load_theme
from .water_cycle import PHASES, water_scene
from .validation import validate_storyboard


def cached_audio(scene: dict, cache: Path) -> tuple[AudioSegment, Path]:
    wanted = scene['narration_text'].split()
    for manifest in sorted(cache.glob('timed_*.json')):
        try:
            records = json.loads(manifest.read_text())
            if ' '.join(item['text'] for item in records).split() != wanted:
                continue
            path = manifest.with_suffix('.wav')
            timings = tuple(NarrationTiming(**item) for item in records)
            duration = soundfile.info(path).duration
            if abs(sum(t.duration for t in timings)-duration) > 0.03:
                continue
            return AudioSegment(path, duration, timings), manifest
        except (OSError, ValueError, KeyError, TypeError):
            continue
    raise ValueError(f'No complete cached Kokoro audio matches {scene["title"]!r}. Generate this lesson normally first; the benchmark will not call Ollama or regenerate speech.')


def build_cached_preview(lesson_path: Path, audio_cache: Path, output: Path, *, theme=None):
    """Select complete introductory sentences naming each phase (45-60s total)."""
    lesson = json.loads(lesson_path.read_text())
    theme = theme or load_theme()
    board_scenes, baseline, arrays, provenance, selected_audio = [], [], [], [], []
    rate = None
    steps = {
        'evaporation': ['Sunlight heats liquid water', 'Water enters the air as vapour'],
        'condensation': ['Rising vapour cools', 'Droplets form clouds'],
        'precipitation': ['Droplets collect in clouds', 'Water falls back to the ground'],
        'collection': ['Water flows or seeps into the ground', 'Water returns to lakes and oceans'],
    }
    output.mkdir(parents=True, exist_ok=True)
    for phase in PHASES:
        source = next((s for s in lesson['scenes'] if phase in s['title'].lower()), None)
        if source is None:
            raise ValueError(f'Cached lesson is missing the essential concept {phase}. No scenes will be invented or discarded.')
        audio, manifest = cached_audio(source, audio_cache)
        chosen, total = [], 0.0
        for timing in audio.timings:
            chosen.append(timing)
            total = timing.start+timing.duration
            text = ' '.join(t.text for t in chosen)
            if total >= 9 and text.rstrip().endswith(('.', '!', '?')) and phase in text.lower():
                break
        else:
            raise ValueError(f'No coherent sentence-boundary excerpt explains {phase} in the cached narration.')
        if total > 20:
            raise ValueError(f'{phase} needs {total:.1f}s before its definition is complete. Choose another cached lesson; narration will not be cut mid-explanation.')
        data, current_rate = soundfile.read(audio.path, dtype='int16', always_2d=True)
        if rate is not None and current_rate != rate:
            raise ValueError('Cached narration sample rates differ. Reuse a single voice/speed configuration.')
        rate = current_rate
        data = data[:round(total*rate)]
        arrays.append(data)
        duration = len(data)/rate
        path = output/f'{phase}.wav'
        soundfile.write(path, data, rate, subtype='PCM_16')
        selected_audio.append(AudioSegment(path, duration, tuple(chosen)))
        identity = source.get('scene_id') or phase
        source_events = source.get('visual_timeline') or []
        cues = [NarrationCue(id=f'{phase}-cue-{i}', text=t.text, start=t.start, duration=t.duration,
            concepts=[phase], source_scene_id=identity,
            source_event_id=(source_events[t.cue_index] if t.cue_index < len(source_events) else {}).get('event_id', f'{identity}:cue:{t.cue_index}'))
            for i, t in enumerate(chosen)]
        board_scenes.append(water_scene(phase, phase, cues, theme, source.get('learning_objective', '')))
        events = [VisualEvent(narration_text=t.text, start_time=t.start, animation_duration=t.duration,
            event_id=f'{phase}-event-{i}', event_order=i, parameters={'step_index':min(1,i*2//len(chosen))}).model_dump()
            for i, t in enumerate(chosen)]
        baseline.append({'type':'process', 'title':phase.title(), 'duration':duration,
            'scene_id':phase, 'parent_scene_id':phase, 'steps':steps[phase], 'narration_text':text,
            'visual_timeline':events, 'animation_params':{'timing_source':'measured'}})
        provenance.append({'phase':phase, 'source_scene_id':identity, 'manifest':str(manifest.resolve()),
            'audio':str(audio.path.resolve()), 'audio_sha256':hashlib.sha256(audio.path.read_bytes()).hexdigest(),
            'start_sample':0, 'end_sample':len(data), 'sample_rate':rate, 'text':text})
    total = sum(s.duration for s in board_scenes)
    if not 45 <= total <= 60:
        raise ValueError(f'Complete cached definitions total {total:.2f}s, outside the 45-60s comparison range. Choose another cached lesson; no padding, speed changes or semantic truncation will be applied.')
    narration = output/'shared_narration.wav'
    soundfile.write(narration, np.concatenate(arrays), rate, subtype='PCM_16')
    digest = hashlib.sha256(narration.read_bytes()).hexdigest()
    board = validate_storyboard(Storyboard(title='The water cycle', topic='water_cycle', theme=theme,
        required_concepts=list(PHASES), scenes=board_scenes,
        provenance={'lesson':str(lesson_path.resolve()), 'narration_sha256':digest, 'audio_origin':'cached Kokoro; complete sentence-boundary excerpts; no LLM calls'}))
    (output/'storyboard.json').write_text(board.model_dump_json(indent=2))
    (output/'baseline.lesson.json').write_text(json.dumps({'title':'The water cycle', 'scenes':baseline}, indent=2))
    (output/'provenance.json').write_text(json.dumps({'narration_sha256':digest, 'segments':provenance}, indent=2))
    return board, narration, {'title':'The water cycle', 'scenes':baseline}
