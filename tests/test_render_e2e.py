"""Real MathTex -> Manim -> FFmpeg -> audio/video test; no LLM or TTS downloads."""
import json
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile
from rich.console import Console

from src.schemas import VisualEvent
from src.tts import AudioSegment, NarrationTiming
from src.video.mathematics import mathtex
from src.video.renderer import render_lesson
from src.duration import prepare_audio_lesson, validate_measured_duration
from src.video.timeline import prepare_visual_lesson, narration_units
from src.video.sequence import validate_sequence


AVAILABLE = all(shutil.which(name) for name in ('latex', 'dvisvgm', 'ffmpeg', 'ffprobe'))

class QuietReporter:
    console = Console(file=io.StringIO())
    def __getattr__(self, name):
        return lambda *args: None


@unittest.skipUnless(AVAILABLE, 'Install LaTeX, dvisvgm, and FFmpeg to run real rendering tests')
class RenderingEndToEndTests(unittest.TestCase):
    def test_tangent_motion_and_math_in_legacy_process_labels(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texts = ['Measure the slope.', 'Move against the gradient.', 'Check the new loss.']
            scenes = [
                {'type': 'graph', 'title': 'Tangent and downhill motion', 'equation': 'f(x)=x^2',
                 'visual_template': 'tangent', 'animation_params': {'initial_x': 3, 'learning_rate': 0.2, 'iterations': 2}},
                {'type': 'process', 'title': 'Mathematical process labels',
                 'steps': ['g=2x', r'x_{t+1}=x_t-\alpha g']},
            ]
            audio = []
            for index, scene in enumerate(scenes):
                scene.update(duration=3.35, narration_text=' '.join(texts), visual_timeline=[
                    VisualEvent(narration_text=text, action=action, start_time=i,
                        animation_duration=1, parameters={'iteration': 0, 'step_index': min(i, 1)}).model_dump()
                    for i, (text, action) in enumerate(zip(texts, ['gradient', 'update', 'loss']))])
                path = root/f'audio_{index}.wav'
                soundfile.write(path, np.zeros(24000), 8000)
                audio.append(AudioSegment(path, 3, tuple(NarrationTiming(text, i, 1, i) for i, text in enumerate(texts))))
            with patch.dict(os.environ, {'EDUGEN_TEX_DIR': str(root/'tex')}):
                result = render_lesson({'title': 'Tangent regression', 'scenes': scenes}, root/'motion.mp4',
                    quality='l', audio_segments=audio, reporter=QuietReporter())
            positions = []
            for second in (0.9, 1.3, 1.8):
                png = subprocess.check_output(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', str(second),
                    '-i', str(result), '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-threads', '1', '-'])
                pixels = np.asarray(Image.open(io.BytesIO(png)).convert('RGB')).astype(float)
                red = (pixels[:,:,0] > 150) & (pixels[:,:,0] > 1.5*pixels[:,:,1]) & (pixels[:,:,0] > 1.5*pixels[:,:,2])
                self.assertTrue(red.any(), 'The optimization point must remain visible during movement')
                positions.append(float(np.where(red)[1].mean()))
            self.assertGreater(positions[0], positions[1]+5)
            self.assertGreater(positions[1], positions[2]+5)

    def test_common_mathtex_expressions_compile(self):
        from manim import tempconfig
        with tempfile.TemporaryDirectory() as directory, tempconfig({'tex_dir': directory}):
            for expression in (r'\theta_{t+1}=\theta_t-\alpha\nabla L(\theta_t)',
                               r'\frac{\partial L}{\partial w_i}', r'\sqrt{x^2+y^2}', 'f(x)=x²',
                               'x₀=3', 'x₁=x₀-α∇ f(x₀)'):
                with self.subTest(expression=expression):
                    self.assertGreater(mathtex(expression).width, 0)

    def test_render_templates_mux_audio_and_generate_event_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenes = [
                {'type': 'equation', 'title': 'Gradient descent update',
                 'equation': r'\theta_{t+1}=\theta_t-\alpha\nabla L(\theta_t)'},
                {'type': 'graph', 'title': 'Computed quadratic descent',
                 'animation_params': {'initial_x': 3, 'learning_rate': 0.2, 'iterations': 2}},
                {'type': 'worked_example', 'title': 'Numerical update', 'equation': 'f(x)=x^2',
                 'steps': ['Compute gradient', 'Apply learning rate'],
                 'animation_params': {'demo': 'quadratic_descent', 'iterations': 2}},
                {'type': 'process', 'title': 'Readable process',
                 'steps': ['Initialize parameters', 'Calculate the gradient', 'Apply the update', 'Check convergence']},
                {'type': 'comparison', 'title': 'Learning rate tradeoff', 'left': 'Small: slower steps',
                 'right': 'Large: risk of overshoot', 'relationship': 'Balance speed and stability'},
                {'type': 'summary', 'title': 'Summary', 'bullets': ['Compute slope', 'Update parameters']},
                {'type': 'title', 'title': 'Optimization explained', 'subtitle': 'Learn through a numerical example'},
                {'type': 'worked_example', 'title': 'General mathematical example',
                 'equation': r'E=\frac{1}{2}mv^2',
                 'steps': ['Set the mass: m=2', 'Set the speed: v=3', 'Calculate energy: E=9']},
                {'type': 'graph', 'graph_type': 'parabola', 'title': 'A shifted function', 'equation': 'y=(x-1)^2'},
            ]
            audio = []
            for index, scene in enumerate(scenes):
                texts = ['Calculate the gradient.', 'Apply the update.', 'Compare the new loss.']
                events = [VisualEvent(narration_text=text, narration_segment=i, action=action,
                                      start_time=i, animation_duration=1,
                                      parameters={'iteration': 0}).model_dump()
                          for i, (text, action) in enumerate(zip(texts, ['gradient', 'update', 'loss']))]
                scene.update(duration=3.35, narration_text=' '.join(texts), visual_timeline=events)
                path = root / f'audio_{index}.wav'
                soundfile.write(path, 0.05*np.sin(np.arange(24000*3)*2*np.pi*220/24000), 24000)
                audio.append(AudioSegment(path, 3, tuple(NarrationTiming(text, i, 1, i) for i, text in enumerate(texts))))
            output = Path(os.environ.get('EDUGEN_E2E_OUTPUT', str(root / 'lesson.mp4')))
            with patch.dict(os.environ, {'EDUGEN_TEX_DIR': str(root/'tex')}):
                result = render_lesson({'title': 'Visual quality regression', 'scenes': scenes}, output,
                                       quality='l', audio_segments=audio, reporter=QuietReporter())
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(result)]))
            self.assertEqual({s['codec_type'] for s in probe['streams']}, {'audio', 'video'})
            self.assertAlmostEqual(float(probe['format']['duration']), 3*len(scenes), delta=0.8)
            video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            self.assertEqual(video['avg_frame_rate'], '30/1')
            previews = result.parent / (result.stem + '_previews')
            for index in range(1, len(scenes)+1):
                report = json.loads((previews / f'scene_{index:03d}' / 'layout.json').read_text())
                self.assertEqual(len(report), 3)
                frames = list((previews / f'scene_{index:03d}').glob('event_*.png'))
                self.assertEqual(len(frames), 3)
                self.assertNotEqual(frames[0].read_bytes(), frames[-1].read_bytes())

    def test_complete_four_minute_fixed_lesson_with_ordered_split_and_summary(self):
        """Real 240s render and mux; deterministic tone stands in for downloaded TTS.

        Tests the same measured AudioSegment contract as Kokoro without requiring
        Ollama, network access, a GPU or speech model inference.
        """
        fixture = Path(__file__).parent/'fixtures'/'gradient_descent_quality.json'
        plan = prepare_visual_lesson(json.loads(fixture.read_text()))
        durations = [24, 24, 60, 24, 24, 24, 36, 24]
        with tempfile.TemporaryDirectory() as directory:
            root, audio = Path(directory), []
            for index, (scene, seconds) in enumerate(zip(plan['scenes'], durations)):
                pairs = [(cue_index, text) for cue_index, cue in enumerate(scene['visual_timeline'])
                         for text in narration_units(cue['narration_text'])]
                length = seconds/len(pairs)
                self.assertLessEqual(length, 8)
                timings = tuple(NarrationTiming(text, i*length, length, cue_index)
                                for i, (cue_index, text) in enumerate(pairs))
                path = root/f'speech_contract_{index}.wav'
                samples = np.arange(8000*seconds)
                soundfile.write(path, 0.05*np.sin(samples*2*np.pi*(180+index*20)/8000), 8000)
                audio.append(AudioSegment(path, seconds, timings))
            prepared = prepare_audio_lesson(plan, audio, None)
            self.assertEqual(prepared.split_count, 1)
            self.assertEqual(len(prepared.lesson['scenes']), 9)
            validate_sequence(prepared.lesson, plan)
            validate_measured_duration(prepared.total_audio_duration, 4)
            self.assertEqual(prepared.lesson['scenes'][-1]['visual_template'], 'summary')
            with patch.dict(os.environ, {'EDUGEN_TEX_DIR': str(root/'tex')}):
                result = render_lesson(prepared.lesson, root/'complete_lesson.mp4', quality='l',
                    audio_segments=prepared.audio_segments, reporter=QuietReporter())
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(result)]))
            self.assertEqual({s['codec_type'] for s in probe['streams']}, {'audio', 'video'})
            self.assertAlmostEqual(float(probe['format']['duration']), 240, delta=1)
            previews = root/'complete_lesson_previews'
            reports = [json.loads((previews/f'scene_{i+1:03d}'/'layout.json').read_text()) for i in range(9)]
            self.assertEqual({event['template'] for report in reports for event in report},
                {'concept', 'tangent', 'process', 'derivation', 'learning_rates', 'numerical', 'convergence', 'summary'})
            step_order = [event['step_index'] for report in reports for event in report if event['step_index'] is not None]
            self.assertEqual(step_order, sorted(step_order))
            self.assertEqual(set(step_order), {0, 1, 2, 3})
            self.assertTrue(all(label['font_size'] >= 27.9 for report in reports for event in report for label in event['labels']))
            self.assertEqual(reports[-1][-1]['template'], 'summary')
            timed_plan = json.loads(result.with_suffix('.lesson.json').read_text())
            self.assertEqual([s['scene_id'] for s in timed_plan['scenes']], [s['scene_id'] for s in prepared.lesson['scenes']])
