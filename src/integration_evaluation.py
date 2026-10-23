"""Offline fixture regression, NOT live prompt/model accuracy.

Validate without TTS/rendering::

    .venv/bin/python -B -m src.integration_evaluation --validate-only
    .venv/bin/python -B -m src.integration_evaluation --check-dependencies

Explicit full runs (real Kokoro, existing local model/voice assets only)::

    .venv/bin/python -B -m src.integration_evaluation --subjects water math --quality m
    .venv/bin/python -B -m src.integration_evaluation --subject signals --baseline

The injected client replays a derived outline and the unmodified lesson JSON
through LessonPlanner's normal two-stage validation. It makes zero model calls.
The primary path is prepare_visual_lesson -> KokoroTTS -> prepare_audio_lesson
-> render_illustrated_lesson, including native math when supported upstream.
--baseline uses render_lesson with the SAME measured audio, without another TTS
pass. No fallback hides primary renderer failures. Previews stay with each MP4.

Expect the main agent's concept-based storyboard selection, native_scene math
adapter and template metadata. Older implementations can fail these regressions.
Math fixtures carry audited prepared_visuals and one-iteration cues so visual
preparation retains their short narration and computed update unchanged.
Coverage measures lexical evidence and metadata, not scientific correctness or
human visual assessment. Generic fixture titles deliberately carry no subject.
--audio-cache is a read-only seed; writable audio/storyboard caches live inside
the evaluation output. --overwrite archives an owned prior run in a sibling
directory and refuses unrelated original directories.
Measured audio uses the application's validate_measured_duration with a default
target of 1 minute and tolerance of 15 percent; fixture estimates remain 50s.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import importlib.metadata
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from unittest.mock import patch

from rich.console import Console

from .planner import LessonPlanner
from .quality import validate_lesson_quality
from .tts import KokoroTTS
from .validator import validate_outline, validate_plan
from .video.timeline import prepare_visual_lesson


FIXTURES = Path(__file__).resolve().parents[1] / 'tests/fixtures/multisubject'
NOTICE = 'OFFLINE FIXTURE REGRESSION; NOT live prompt/model accuracy; zero LLM calls.'
OWNER = '.integration-evaluation.json'

# Independent expectations: do not derive expected coverage from selected visuals.
# Aliases are literal phrases after punctuation/hyphen normalization.
SUBJECTS = {
    'water': {
        'prompt': 'Explain the water cycle: evaporation, condensation, precipitation, and collection.',
        'concepts': {
            'evaporation': ['evaporation', 'evaporates'],
            'condensation': ['condensation', 'condense'],
            'precipitation': ['precipitation'],
            'collection': ['collection', 'collects'],
        },
    },
    'math': {
        'prompt': 'Explain gradient descent for f(x)=x^2, initial x=3, learning rate 0.2, with one numerical update.',
        'concepts': {
            'quadratic loss': ['quadratic loss', 'x squared', 'x^2'],
            'gradient': ['gradient', 'tangent slope'],
            'learning rate': ['learning rate'],
            'parameter update': ['parameter update', 'update', 'subtract'],
            'convergence': ['convergence', 'minimum', 'slope is nearly zero'],
        },
    },
    'networking': {
        'prompt': 'Explain IP packet forwarding through routers and TCP endpoint acknowledgments, retransmission, and ordered delivery.',
        'concepts': {
            'IP packets': ['IP packets', 'IP packet', 'packets'],
            'routers': ['routers', 'router'],
            'TCP acknowledgments': ['TCP acknowledgments', 'TCP acknowledgment', 'acknowledgment', 'ACK'],
            'sequence numbers': ['sequence numbers', 'sequence number'],
            'retransmission': ['retransmission', 'retransmit', 'retry'],
            'ordered delivery': ['ordered delivery', 'restore order', 'ordered stream'],
        },
    },
    'signals': {
        'prompt': 'Explain qualitative low-pass filtering of a sensor signal, slower changes, high-frequency variation, cutoff choice, and possible delay.',
        'concepts': {
            'low-pass filter': ['low pass filter', 'low pass filtering'],
            'slower changes': ['slower changes', 'slow trend'],
            'high-frequency variation': ['high frequency variation', 'rapid ripple'],
            'cutoff choice': ['cutoff choice', 'cutoff'],
            'delay': ['delay'],
        },
    },
}


class FixtureOllamaClient:
    """Only two predetermined JSON responses; never contacts Ollama or repairs."""

    model = 'fixture-replay-no-model'

    def __init__(self, lesson):
        sections = []
        for index, scene in enumerate(lesson['scenes']):
            kind = scene['type']
            sections.append({
                'section_id': f'section-{index + 1}',
                # Planner reserves an outline summary by this conventional name.
                # Actual fixture/renderer titles remain generic.
                'title': 'Summary' if kind == 'summary' else scene['title'],
                'learning_objective': scene['learning_objective'],
                'key_concepts': scene['important_concepts'],
                'visual_type': 'concepts' if kind in {'bullets', 'title', 'summary'} else kind,
            })
        outline = {
            'title': lesson['title'], 'audience': 'beginner',
            'learning_goals': [s['learning_objective'] for s in lesson['scenes']][:6],
            'sections': sections,
        }
        validate_outline(outline)
        self.responses = [outline, lesson]
        self.calls = 0

    def generate_json(self, prompt, schema):
        if self.calls >= len(self.responses):
            raise RuntimeError('Fixture required a planner repair; deterministic regression failed.')
        response = self.responses[self.calls]
        self.calls += 1
        return json.dumps(response)


def load_and_plan(subject):
    """Schema, content, length, then actual injected two-stage planner."""
    lesson = json.loads((FIXTURES / f'{subject}.json').read_text(encoding='utf-8'))
    validate_plan(lesson)
    if lesson['title'] != 'A closer look' or [s['title'] for s in lesson['scenes']] != [
            'Getting started', 'Following the changes', 'Making connections', 'Putting it together']:
        raise ValueError(f'{subject}: keep fixture titles identical across subjects to test concept selection.')
    words = sum(len(s['narration_text'].split()) for s in lesson['scenes'])
    seconds = sum(s['duration'] for s in lesson['scenes'])
    if not (3 <= len(lesson['scenes']) <= 4 and 90 <= words <= 130 and 35 <= seconds <= 60):
        raise ValueError(f'{subject}: fixture scene/word/duration budget failed.')
    validate_lesson_quality(lesson, seconds / 60, prompt=SUBJECTS[subject]['prompt'], require_summary=True)
    client = FixtureOllamaClient(lesson)
    planned = LessonPlanner(client=client).plan(SUBJECTS[subject]['prompt'], seconds / 60, force=True)
    if client.calls != 2 or planned != validate_plan(lesson).model_dump(mode='json', exclude_defaults=True):
        raise ValueError(f'{subject}: planner replay changed fixture content or call count.')
    return planned, {'fixture_words': words, 'fixture_estimated_seconds': seconds,
                     'fixture_response_calls': client.calls, 'model_calls': 0}


def normalize(value):
    return ' '.join(re.findall(r'[a-z0-9]+', value.lower()))


def coverage(expected, values):
    haystacks = [' ' + normalize(v) + ' ' for v in values if isinstance(v, str)]
    matches = {concept: [alias for alias in aliases if any(
        ' ' + normalize(alias) + ' ' in text for text in haystacks)]
        for concept, aliases in expected.items()}
    covered = [key for key, hits in matches.items() if hits]
    return {'covered': len(covered), 'expected': len(expected),
            'fraction': len(covered) / len(expected), 'matched_aliases': matches,
            'missing': [key for key in expected if key not in covered]}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def storyboard_metrics(board, expected, lesson=None):
    scenes = board['scenes']
    source_types = {s['scene_id']: s['type'] for s in (lesson or {}).get('scenes', [])}
    templates = [s.get('template', '') for s in scenes]
    # Do not present alternating layout directions as different templates.
    counts = Counter(t for t in templates if t)
    evidence = []
    for scene in scenes:
        if scene.get('native_scene'):
            # Native math content is audited by the existing renderer. This is
            # lexical content evidence, not a claim about rendered visibility.
            evidence.extend(strings(scene['native_scene']))
        else:
            objects = {o['id']: o for o in scene.get('objects', [])}
            for motion in scene.get('motions', []):
                obj = objects.get(motion.get('object_id'), {})
                if obj.get('role') == 'illustration':
                    evidence.extend(motion.get('concepts', []))
                    evidence.extend(obj.get('concepts', []))
    return {
        'scene_count': len(scenes), 'templates': templates,
        'template_counts': dict(counts), 'unique_template_count': len(counts),
        'template_variety_fraction': len(counts) / max(1, len(scenes)),
        'missing_template_metadata': sum(not t for t in templates),
        'layouts': [s['layout'] for s in scenes],
        'native_math_scenes': sum(bool(s.get('native_scene')) for s in scenes),
        'concept_metadata_coverage': coverage(expected, [c for s in scenes for c in s.get('concepts', [])]),
        'visual_evidence_coverage': coverage(expected, evidence),
        'selection': [{k: s.get(k) for k in ('id', 'template', 'selection_reason', 'selection_assets')}
                      for s in scenes],
        'scenes': [{'id': s['id'], 'source_scene_id': s.get('source_scene_id') or s['id'],
                    'type': source_types.get(s.get('source_scene_id') or s['id'])
                            or (s.get('native_scene') or {}).get('type'),
                    'native_type': (s.get('native_scene') or {}).get('type'),
                    'template': s.get('template', ''), 'layout': s['layout'],
                    'narration_text': ' '.join(c['text'] for c in s.get('narration', []))}
                   for s in scenes],
    }


def narration_snapshot(lesson):
    scenes = [{'scene_id': s.get('scene_id'), 'type': s['type'],
               'template': s.get('visual_template', ''), 'title': s['title'],
               'narration_text': s.get('narration_text', '')} for s in lesson['scenes']]
    text = ' '.join(s['narration_text'] for s in scenes)
    final = scenes[-1] if scenes else None
    return {'full_text': text, 'word_count': len(text.split()), 'scene_count': len(scenes),
            'sha256': hashlib.sha256(text.encode()).hexdigest(), 'scenes': scenes,
            'final_scene_is_summary': bool(final and final['type'] == 'summary'),
            'final_summary': final if final and final['type'] == 'summary' else None}


def narration_preservation(visual, prepared, segments, board=None):
    """Compare every spoken word and scene boundary; math preparation may rewrite fixtures."""
    reference = [s.get('narration_text', '').split() for s in prepared['scenes']]
    audio = [[word for timing in segment.timings for word in timing.text.split()] for segment in segments]
    final_summary = bool(prepared['scenes'] and prepared['scenes'][-1]['type'] == 'summary')
    result = {
        'visual_to_prepared_all_words_preserved':
            narration_snapshot(visual)['full_text'].split() == [w for words in reference for w in words],
        'prepared_to_audio_all_scenes_preserved': reference == audio,
        'final_scene_is_summary': final_summary,
        'summary_preserved_in_audio': final_summary and bool(audio) and reference[-1] == audio[-1],
    }
    if board is not None:
        board_text = [[w for cue in s.get('narration', []) for w in cue['text'].split()] for s in board['scenes']]
        expected_ids = [s['scene_id'] for s in prepared['scenes']]
        actual_ids = [s.get('source_scene_id') or s['id'] for s in board['scenes']]
        result.update(
            prepared_to_storyboard_all_scenes_preserved=reference == board_text and expected_ids == actual_ids,
            summary_preserved_in_storyboard=final_summary and bool(board_text)
                and reference[-1] == board_text[-1] and expected_ids[-1] == actual_ids[-1],
            storyboard_full_text=' '.join(' '.join(words) for words in board_text),
        )
    return result


class PipelineReporter:
    """Accept the existing planner/TTS/render reporter events and persist them."""

    def __init__(self, path):
        self.console = Console(highlight=False)
        self.logger = logging.Logger(f'integration.{path.parent.name}')
        self.handler = logging.FileHandler(path, mode='w', encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        self.logger.addHandler(self.handler)

    def event(self, name, *values):
        line = f'{name}: ' + ' '.join(str(v) for v in values)
        self.logger.info(line)
        if name != 'verbose_line' and name != 'scene_frame_progress':
            self.console.print(line, markup=False)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return lambda *values: self.event(name, *values)

    def close(self):
        self.handler.close()
        self.logger.removeHandler(self.handler)


class OfflineKokoroTTS(KokoroTTS):
    """Use existing PCM caches first, then real locally installed Kokoro."""

    uncached_phrase_calls = 0

    def _load_dependencies(self):
        if self._pipeline is None:
            try:
                importlib.metadata.distribution('en_core_web_sm')
            except importlib.metadata.PackageNotFoundError as exc:
                # Misaki would otherwise invoke spacy.cli.download in a child
                # process, which is outside Python socket guards.
                raise RuntimeError('Offline Kokoro needs an already installed en_core_web_sm package.') from exc
        self.uncached_phrase_calls += 1
        return super()._load_dependencies()


def check_offline_dependencies():
    """Load real cached model, G2P, and voice; never invoke the audio generator."""
    packages = ['kokoro', 'torch', 'soundfile', 'misaki', 'spacy', 'en_core_web_sm', 'huggingface_hub', 'rich']
    versions = {name: importlib.metadata.version(name) for name in packages}
    from huggingface_hub import hf_hub_download
    files = {}
    for filename in ('config.json', 'kokoro-v1_0.pth', 'voices/af_heart.pt'):
        path = Path(hf_hub_download(repo_id='hexgrad/Kokoro-82M', filename=filename, local_files_only=True))
        files[filename] = {'path': str(path), 'bytes': path.stat().st_size}
    tts = OfflineKokoroTTS()
    # Dependency initialization is not a synthesis/cache miss.
    pipeline, soundfile = KokoroTTS._load_dependencies(tts)
    voice = pipeline.load_voice(tts.voice)
    return {'packages': versions, 'cached_assets': files, 'model_loaded': pipeline.model is not None,
            'english_g2p_loaded': pipeline.g2p is not None, 'voice_shape': list(voice.shape),
            'libsndfile_version': soundfile.__libsndfile_version__,
            'tts_run': False, 'render_run': False, 'model_calls': 0}


@contextmanager
def offline_runtime():
    def blocked(*args, **kwargs):
        raise RuntimeError('Network access is disabled for fixture regression; use existing local assets.')

    with patch.dict(os.environ, {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                                 'HF_HUB_DISABLE_TELEMETRY': '1'}), \
            patch('socket.create_connection', blocked), \
            patch('socket.socket.connect', blocked), patch('socket.socket.connect_ex', blocked):
        yield


@contextmanager
def stage(report, reporter, name):
    started = time.perf_counter()
    reporter.event('stage_started', name)
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        report['stage_wall_seconds'][name] = round(elapsed, 4)
        reporter.event('stage_finished', name, f'{elapsed:.3f}s')


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def prepare_output(output, overwrite):
    """Archive only recognized evaluation runs; never move arbitrary originals."""
    if output.is_symlink():
        raise ValueError('Evaluation output cannot be a symlink.')
    output = output.resolve()
    if output.exists():
        if not overwrite:
            raise ValueError(f'{output} already exists; choose another directory or pass --overwrite.')
        owned = False
        for marker in (output / OWNER, output / 'metrics.json'):
            try:
                owned = owned or json.loads(marker.read_text()).get('notice') == NOTICE
            except (OSError, ValueError, AttributeError):
                pass
        if not owned:
            raise ValueError(f'{output} is not an evaluation-owned directory; original artifacts are protected.')
        backup = output.with_name(f'{output.name}.previous-{time.time_ns()}')
        output.rename(backup)
        print(f'Previous evaluation preserved: {backup}', flush=True)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / OWNER, {'notice': NOTICE})
    return output


def seed_audio_cache(source, destination):
    """Copy source PCM/manifest files into a fresh owned cache, never hardlink."""
    destination.mkdir(parents=True, exist_ok=False)
    count = 0
    if source.exists():
        if not source.is_dir():
            raise ValueError(f'Audio cache seed is not a directory: {source}')
        for path in sorted(source.iterdir()):
            if path.is_file() and path.suffix in {'.wav', '.json'}:
                shutil.copyfile(path, destination / path.name)
                count += 1
    return {'read_only_seed': str(source), 'writable_cache': str(destination), 'copied_files': count}


def inspect_video(path, expected_seconds):
    probe = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], text=True))
    streams = probe['streams']
    stream_types = {s['codec_type'] for s in streams}
    if not {'audio', 'video'} <= stream_types:
        raise RuntimeError(f'{path}: missing audio/video stream.')
    # Full decode of BOTH streams, with error exit status rather than probe alone.
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-i', str(path),
                    '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                   check=True, capture_output=True, text=True)
    duration = float(probe['format']['duration'])
    stream_durations = [float(s['duration']) for s in streams if s.get('duration') not in (None, 'N/A')]
    # Legacy rendering adds .35s to each scene; report that delta explicitly.
    timing_ok = abs(duration - expected_seconds) <= 0.25 and all(
        abs(d - expected_seconds) <= 0.25 for d in stream_durations)
    return {'path': str(path), 'bytes': path.stat().st_size, 'streams': streams,
            'duration_seconds': duration, 'measured_audio_delta_seconds': duration - expected_seconds,
            'duration_matches_audio': timing_ok, 'audio_video_decode_ok': True}


def evaluate_subject(subject, args, output):
    from .duration import DurationConstraintError, prepare_audio_lesson, validate_measured_duration
    from .video.illustrated_pipeline import render_illustrated_lesson
    from .video.renderer import render_lesson

    folder = output / subject
    folder.mkdir(parents=True, exist_ok=True)
    reporter = PipelineReporter(folder / 'execution.log')
    started = time.perf_counter()
    report = {'subject': subject, 'notice': NOTICE, 'status': 'failed',
              'stage_wall_seconds': {}, 'renderers': {}, 'failures': [],
              'expected_concepts': SUBJECTS[subject]['concepts'],
              'coverage_method': 'Literal aliases in narration, concept metadata, and motion/native-math evidence; not semantic or visual accuracy.'}
    try:
        with stage(report, reporter, 'prompt_fixture_planner'):
            plan, details = load_and_plan(subject)
            report.update(details)
            save_json(folder / 'planned.lesson.json', plan)
            report['narration'] = {'fixture': narration_snapshot(plan)}
        expected = SUBJECTS[subject]['concepts']
        report['fixture_narration_coverage'] = coverage(expected, [s['narration_text'] for s in plan['scenes']])
        with stage(report, reporter, 'prepare_visual_lesson'):
            visual = prepare_visual_lesson(plan, prompt=SUBJECTS[subject]['prompt'])
            validate_plan(visual)
            report['prepared_words'] = sum(len(s['narration_text'].split()) for s in visual['scenes'])
            report['narration']['visual'] = narration_snapshot(visual)
            report['narration']['visual_preparation_changed_fixture_text'] = (
                report['narration']['fixture']['full_text'].split()
                != report['narration']['visual']['full_text'].split())
        tts = OfflineKokoroTTS(cache_dir=output / 'cache/audio', reporter=reporter)
        with stage(report, reporter, 'kokoro_actual_or_cached'):
            audio = tts.synthesize_plan(visual)
        report['uncached_tts_phrase_calls'] = tts.uncached_phrase_calls
        with stage(report, reporter, 'prepare_audio_lesson'):
            prepared = prepare_audio_lesson(visual, audio, tts, reporter=reporter)
            save_json(folder / 'prepared.lesson.json', prepared.lesson)
            report['narration']['prepared'] = narration_snapshot(prepared.lesson)
            report['narration']['preservation'] = narration_preservation(
                visual, prepared.lesson, prepared.audio_segments)
        report['measured_audio_seconds'] = prepared.total_audio_duration
        target = args.duration * 60
        report['duration_evaluation'] = {
            'target_minutes': args.duration, 'target_seconds': target,
            'tolerance': args.duration_tolerance,
            'allowed_seconds': [target * (1 - args.duration_tolerance), target * (1 + args.duration_tolerance)],
            'measured_seconds': prepared.total_audio_duration,
            'validator': 'src.duration.validate_measured_duration',
        }
        try:
            validate_measured_duration(prepared.total_audio_duration, args.duration, args.duration_tolerance)
        except DurationConstraintError as exc:
            report['duration_evaluation'].update(passed=False, error=str(exc))
        else:
            report['duration_evaluation']['passed'] = True
        report['audio_split_count'] = prepared.split_count
        report['scene_audio'] = []
        for index, (scene, segment) in enumerate(zip(prepared.lesson['scenes'], prepared.audio_segments), 1):
            item = {'index': index, 'scene_id': scene['scene_id'], 'title': scene['title'],
                    'type': scene['type'], 'visual_type': scene.get('visual_type', ''),
                    'legacy_template': scene.get('visual_template', ''),
                    'narration_text': scene['narration_text'],
                    'actual_tts_seconds': segment.duration, 'path': str(segment.path.resolve()),
                    'source_audio_sha256': hashlib.sha256(segment.path.read_bytes()).hexdigest(),
                    'measured_phrase_count': len(segment.timings)}
            report['scene_audio'].append(item)
            reporter.event('measured_scene_audio', json.dumps(item))
        report['prepared_narration_coverage'] = coverage(expected, [s['narration_text'] for s in prepared.lesson['scenes']])
        if args.baseline:
            legacy_templates = [s.get('visual_template') or s['type'] for s in prepared.lesson['scenes']]
            report['legacy_visuals'] = {
                'templates': legacy_templates,
                'template_counts': dict(Counter(legacy_templates)),
                'unique_template_count': len(set(legacy_templates)),
                'comparison_note': 'Legacy Manim dispatch templates; primary uses storyboard template metadata.',
            }
        for name in (['primary', 'legacy'] if args.baseline else ['primary']):
            destination = folder / f'{name}.mp4'
            try:
                with stage(report, reporter, f'{name}_render'):
                    if name == 'primary':
                        render_illustrated_lesson(deepcopy(prepared.lesson), destination, quality=args.quality,
                                                  renderer='manim', audio_segments=prepared.audio_segments,
                                                  reporter=reporter, cache_dir=output / 'cache/storyboards',
                                                  prompt=SUBJECTS[subject]['prompt'])
                    else:
                        render_lesson(deepcopy(prepared.lesson), destination, quality=args.quality,
                                      audio_segments=prepared.audio_segments, reporter=reporter,
                                      preview_dir=folder / 'legacy_previews')
                with stage(report, reporter, f'{name}_probe_and_decode'):
                    report['renderers'][name] = inspect_video(destination, prepared.total_audio_duration)
            except Exception as exc:
                report['renderers'][name] = {'error': f'{type(exc).__name__}: {exc}'}
                report['failures'].append(f'{name}: {type(exc).__name__}: {exc}')
                reporter.logger.exception('%s failed', name)
            if name == 'primary':
                # The pipeline writes the board before render, so diagnostics
                # survive a failed Manim invocation as well.
                board_path = destination.with_suffix('.storyboard.json')
                if board_path.exists():
                    board_data = json.loads(board_path.read_text())
                    report['storyboard'] = storyboard_metrics(board_data, expected, prepared.lesson)
                    report['narration']['preservation'] = narration_preservation(
                        visual, prepared.lesson, prepared.audio_segments, board_data)
                    by_id = {s['source_scene_id']: s for s in report['storyboard']['scenes']}
                    for item in report['scene_audio']:
                        selected = by_id.get(item['scene_id'], {})
                        item.update(primary_template=selected.get('template'), layout=selected.get('layout'),
                                    native_type=selected.get('native_type'))
        board = report.get('storyboard', {})
        checks = {
            'primary_decode': report['renderers'].get('primary', {}).get('audio_video_decode_ok', False),
            'primary_audio_timing': report['renderers'].get('primary', {}).get('duration_matches_audio', False),
            'duration_budget': report['duration_evaluation']['passed'],
            'narration_coverage': report['prepared_narration_coverage']['fraction'] == 1,
            'storyboard_concept_coverage': board.get('concept_metadata_coverage', {}).get('fraction') == 1,
            'visual_evidence_coverage': board.get('visual_evidence_coverage', {}).get('fraction') == 1,
            'template_metadata': bool(board) and board.get('missing_template_metadata') == 0,
            'template_variety': board.get('unique_template_count', 0) >= 2,
            'fixture_narration_preserved': not report['narration']['visual_preparation_changed_fixture_text'],
        }
        for key in ('visual_to_prepared_all_words_preserved', 'prepared_to_audio_all_scenes_preserved',
                    'prepared_to_storyboard_all_scenes_preserved', 'final_scene_is_summary',
                    'summary_preserved_in_audio', 'summary_preserved_in_storyboard'):
            checks[key] = report['narration']['preservation'].get(key, False)
        if args.baseline:
            checks['legacy_decode'] = report['renderers'].get('legacy', {}).get('audio_video_decode_ok', False)
        report['checks'] = checks
        report['failures'].extend(key for key, passed in checks.items() if not passed)
        report['status'] = 'passed' if not report['failures'] else 'failed'
    except Exception as exc:
        report['failures'].append(f'{type(exc).__name__}: {exc}')
        reporter.logger.exception('Subject pipeline failed')
    finally:
        report['total_wall_seconds'] = round(time.perf_counter() - started, 4)
        save_json(folder / 'metrics.json', report)
        reporter.event('subject_result', subject, report['status'], report['failures'])
        reporter.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--subject', '--subjects', dest='subjects', nargs='+', default=['all'],
                        help='water math networking signals, or all; comma-separated names also accepted')
    parser.add_argument('--output-dir', type=Path, default=Path('outputs/integration_evaluation'))
    parser.add_argument('--audio-cache', type=Path, default=Path('outputs/narration'),
                        help='Read-only cache seed; synthesis writes to OUTPUT_DIR/cache/audio')
    parser.add_argument('--quality', choices=['l', 'm'], default='m')
    parser.add_argument('--duration', type=float, default=1.0, help='Measured evaluation target in minutes (default: 1)')
    parser.add_argument('--duration-tolerance', type=float, default=0.15,
                        help='Measured duration tolerance, 0.05–0.30 (default: 0.15)')
    parser.add_argument('--baseline', action='store_true', help='Also render legacy Manim with identical source narration')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--validate-only', action='store_true', help='No TTS, rendering, subprocesses, or output files')
    parser.add_argument('--check-dependencies', action='store_true',
                        help='Load offline Kokoro model/G2P/voice without synthesis or rendering; can combine with --validate-only')
    args = parser.parse_args(argv)
    if not 0.5 <= args.duration <= 30:
        parser.error('--duration must be between 0.5 and 30 minutes.')
    if not 0.05 <= args.duration_tolerance <= 0.30:
        parser.error('--duration-tolerance must be between 0.05 and 0.30.')
    selected = list(dict.fromkeys(part for value in args.subjects for part in value.split(',')))
    if selected == ['all']:
        selected = list(SUBJECTS)
    if not selected or any(s not in SUBJECTS for s in selected):
        parser.error('Select water, math, networking, signals, or all.')
    print(NOTICE, flush=True)
    with offline_runtime():
        if args.check_dependencies:
            print(json.dumps({'offline_dependencies': check_offline_dependencies()}), flush=True)
            if not args.validate_only:
                return 0
        if args.validate_only:
            for subject in selected:
                plan, details = load_and_plan(subject)
                visual = prepare_visual_lesson(plan, prompt=SUBJECTS[subject]['prompt'])
                validate_plan(visual)
                print(json.dumps({'subject': subject, **details, 'schema_and_planner': 'passed',
                                  'fixture_narration_coverage': coverage(SUBJECTS[subject]['concepts'],
                                      [s['narration_text'] for s in plan['scenes']]),
                                  'fixture_scene_types': [s['type'] for s in plan['scenes']],
                                  'prepared_legacy_templates': [s.get('visual_template', '') for s in visual['scenes']],
                                  'target_minutes': args.duration, 'duration_tolerance': args.duration_tolerance,
                                  'narration': {'fixture': narration_snapshot(plan), 'visual': narration_snapshot(visual)},
                                  'tts_run': False, 'render_run': False}))
            return 0
        output = args.output_dir.resolve()
        source_cache = args.audio_cache.resolve()
        if output.is_relative_to(source_cache) or source_cache.is_relative_to(output):
            parser.error('Output and read-only audio cache seed must not contain each other.')
        try:
            output = prepare_output(args.output_dir, args.overwrite)
            cache_info = seed_audio_cache(source_cache, output / 'cache/audio')
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        results = [evaluate_subject(subject, args, output) for subject in selected]
        successes = sum(r['status'] == 'passed' for r in results)
        report = {'notice': NOTICE, 'model_calls': 0, 'quality': args.quality, 'audio_cache': cache_info,
                  'target_minutes': args.duration, 'duration_tolerance': args.duration_tolerance,
                  'legacy_comparison_requested': args.baseline,
                  'reliability': {'attempted': len(results), 'passed': successes,
                                  'failed': len(results) - successes,
                                  'success_fraction': successes / len(results),
                                  'interpretation': 'Single run per subject; not a statistical reliability estimate.'},
                  'subjects': results}
        report['primary_template_counts_across_subjects'] = dict(Counter(
            template for result in results for template in result.get('storyboard', {}).get('templates', [])
            if template))
        save_json(output / 'metrics.json', report)
        print(f'{successes}/{len(results)} subjects passed; report: {output / "metrics.json"}', flush=True)
        return 0 if successes == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
