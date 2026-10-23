"""Content, chronology and coverage regressions without model inference."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile

from src.content import validate_content, validate_revision_coverage
from src.duration import prepare_audio_lesson, validate_measured_duration
from src.planner import LessonPlanner, LessonPlanGenerationError
from src.quality import validate_lesson_quality
from src.tts import AudioSegment, NarrationTiming
from src.validator import validate_plan
from src.video.layout import VisualQualityError, validate_contrast
from src.video.mathematics import MathRenderingError, gradient_updates, validate_calculation, quadratic_loss_history
from src.video.sequence import identify_lesson, validate_sequence
from src.video.timeline import prepare_visual_lesson, validate_timeline

FIXTURE = Path(__file__).parent / 'fixtures' / 'gradient_descent_quality.json'


def fixed_lesson():
    return json.loads(FIXTURE.read_text())


class EducationalQualityTests(unittest.TestCase):
    def test_english_allows_mathematical_greek_but_rejects_mixed_scripts(self):
        good = fixed_lesson()
        validate_content(good)
        for field in ('title', 'narration_text'):
            bad = deepcopy(good)
            bad['scenes'][0][field] += ' 更新权重'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'non-English'):
                validate_content(bad)
        good['scenes'][3]['equation'] = 'θ_{t+1}=θ_t-α∇ L(θ_t)'
        validate_content(good)

    def test_incomplete_equations_and_incorrect_arithmetic_are_not_replaced(self):
        for expression in ('x_{t+1}=x_t-', 'x=', r'\frac{}{2}', '3-0.2*6=2.8', '1.8^2=9'):
            with self.subTest(expression=expression), self.assertRaises(MathRenderingError):
                validate_calculation(expression)
        for expression in ('3-0.2*6=1.8', '1.8^2=3.24', '1.08^2=1.1664', r'\frac{6}{2}=3'):
            validate_calculation(expression)
        bad = fixed_lesson()
        bad['scenes'][5]['steps'][1] = 'Update: x_{t+1}=x_t-'
        before = deepcopy(bad)
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            prepare_visual_lesson(bad)
        self.assertEqual(bad, before)
        bad = fixed_lesson()
        bad['scenes'][2]['steps'][2] = r'x_{t+1}=x_t-\alpha'
        with self.assertRaisesRegex(ValueError, 'gradient factor'):
            prepare_visual_lesson(bad)

    def test_bad_spoken_arithmetic_is_reported(self):
        bad = fixed_lesson()
        bad['scenes'][5]['narration_text'] = 'The calculation is 3 - 0.2 = 1.8.'
        with self.assertRaisesRegex(ValueError, 'Incorrect numerical equality'):
            prepare_visual_lesson(bad)
        bad['scenes'][5]['narration_text'] = "At the initial value, f'(3)=5."
        with self.assertRaisesRegex(ValueError, 'incorrect quadratic'):
            prepare_visual_lesson(bad)

    def test_objectives_select_distinct_templates_and_final_summary(self):
        plan = prepare_visual_lesson(fixed_lesson())
        self.assertEqual([s['visual_template'] for s in plan['scenes']],
            ['concept', 'tangent', 'process', 'derivation', 'learning_rates', 'numerical', 'convergence', 'summary'])
        self.assertEqual(plan['scenes'][-1]['type'], 'summary')
        validate_plan(plan)
        self.assertEqual(prepare_visual_lesson(plan), plan)  # no repeated narration rewriting

    def test_missing_cached_summary_is_added_without_dropping_any_scene(self):
        original = fixed_lesson()
        original['scenes'].pop()
        updated = prepare_visual_lesson(original)
        self.assertEqual(len(updated['scenes']), len(original['scenes'])+1)
        self.assertEqual(updated['scenes'][-1]['type'], 'summary')
        self.assertEqual([s['title'] for s in updated['scenes'][:-1]], [s['title'] for s in original['scenes']])

    def test_new_generation_requires_summary_and_repairs_bad_language(self):
        plan = fixed_lesson()
        validate_lesson_quality(plan, 4, prompt='Explain gradient descent', require_summary=True)
        missing = deepcopy(plan)
        missing['scenes'][-1]['type'] = 'bullets'
        with self.assertRaisesRegex(ValueError, 'final scene'):
            validate_lesson_quality(missing, 4, require_summary=True)
        bad = deepcopy(plan)
        bad['scenes'][2]['steps'][0] += ' 初始化'
        from test_expansion import OUTLINE
        class Client:
            def __init__(self): self.calls = []
            def generate_json(self, prompt, schema):
                self.calls.append(prompt)
                return [OUTLINE, bad, plan][len(self.calls)-1]
        client = Client()
        result = LessonPlanner(client).plan('Explain gradient descent', 4)
        self.assertIn('non-English', client.calls[-1])
        self.assertEqual(result['scenes'][2]['steps'][0], plan['scenes'][2]['steps'][0])

    def test_revision_cannot_cut_summary_or_swap_similar_scene_types(self):
        original = identify_lesson(fixed_lesson())
        for mutation in ('drop', 'swap'):
            revised = deepcopy(original)
            if mutation == 'drop': revised['scenes'].pop()
            else: revised['scenes'][1], revised['scenes'][6] = revised['scenes'][6], revised['scenes'][1]
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'scene|sequence'):
                validate_revision_coverage(original, revised)
        class Client:
            model = 'qwen3:4b'
            calls = 0
            def generate_json(self, *args):
                self.calls += 1
                return revised
        client = Client()
        with self.assertRaisesRegex(LessonPlanGenerationError, 'original valid lesson was preserved'):
            LessonPlanner(client).revise_for_duration(original, 'Explain gradient descent', 4, 'too long')
        self.assertEqual(client.calls, 3)
        self.assertEqual(original['scenes'][-1]['type'], 'summary')

    def test_minimum_contrast_and_no_dimming(self):
        self.assertGreater(validate_contrast((1, 1, 1)), 4.5)
        for color, opacity in (((0.2, 0.2, 0.2), 1), ((1, 1, 1), 0.4)):
            with self.assertRaises(VisualQualityError): validate_contrast(color, opacity=opacity)

    def test_old_mixed_language_cache_is_repaired_without_regenerating_outline(self):
        from test_expansion import OUTLINE
        good = fixed_lesson()
        bad = deepcopy(good)
        bad['scenes'][2]['steps'][0] += ' 初始化'
        class Client:
            model = 'qwen3:4b'
            def __init__(self, result): self.result, self.calls = result, []
            def generate_json(self, prompt, schema):
                self.calls.append(prompt)
                return self.result
        with tempfile.TemporaryDirectory() as directory:
            client = Client(good)
            planner = LessonPlanner(client, cache_dir=Path(directory))
            key = planner._cache_key('Explain gradient descent', 4, client.model)
            planner._save_cache('outline', key, OUTLINE)
            planner._save_cache('plan', key, bad)
            result = planner.plan('Explain gradient descent', 4)
            self.assertEqual(len(client.calls), 1)
            self.assertIn('Previous generated JSON', client.calls[0])
            self.assertEqual(result['scenes'][-1]['type'], 'summary')
            # Failed repairs never replace the previously cached source.
            planner._save_cache('plan', key, bad)
            broken = Client(bad)
            with self.assertRaises(LessonPlanGenerationError):
                LessonPlanner(broken, cache_dir=Path(directory)).plan('Explain gradient descent', 4)
            self.assertEqual(len(broken.calls), 3)
            self.assertEqual(planner._load_cache('plan', key), bad)

    def test_quadratic_values_and_convergence_are_deterministic(self):
        states = gradient_updates(iterations=6)
        self.assertEqual(states[0].gradient, 6)
        self.assertAlmostEqual(states[0].next_x, 1.8)
        self.assertAlmostEqual(states[0].next_loss, 3.24)
        for previous, current in zip(states, states[1:]):
            self.assertAlmostEqual(previous.next_x, current.x)
            self.assertLess(current.next_loss, previous.next_loss)
        self.assertAlmostEqual(quadratic_loss_history(0.2)[1], 3.24)
        self.assertLess(quadratic_loss_history(0.2)[-1], quadratic_loss_history(0.05)[-1])
        self.assertGreater(quadratic_loss_history(1.05)[-1], 9)

    def test_authored_demo_parameters_are_not_silently_changed(self):
        source = fixed_lesson()
        source['scenes'][5]['animation_params'] = {'initial_x': 2, 'learning_rate': 0.1, 'iterations': 3}
        example = prepare_visual_lesson(source)['scenes'][5]
        self.assertIn('1.6', example['narration_text'])
        self.assertIn('2.56', example['narration_text'])
        self.assertEqual(example['animation_params']['learning_rate'], 0.1)
        self.assertEqual(example['animation_params']['iterations'], 3)

    def test_process_order_and_all_narration_survive_measured_splitting(self):
        source = prepare_visual_lesson(fixed_lesson())
        scene = source['scenes'][2]
        plan = dict(source, scenes=[scene])
        cues = scene['visual_timeline']
        length = 60/len(cues)
        self.assertLessEqual(length, 8)
        timings = tuple(NarrationTiming(c['narration_text'], i*length, length, i) for i, c in enumerate(cues))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'process.wav'
            soundfile.write(path, np.zeros(60000), 1000)
            class NoTTS:
                def synthesize(self, *args): raise AssertionError('Measured splits must reuse audio')
            result = prepare_audio_lesson(plan, [AudioSegment(path, 60, timings)], NoTTS())
            validate_sequence(result.lesson, identify_lesson(plan))
            steps = [e['parameters']['step_index'] for s in result.lesson['scenes'] for e in s['visual_timeline']]
            self.assertEqual(steps, sorted(steps))
            self.assertEqual(set(steps), {0, 1, 2, 3})
            self.assertEqual(result.lesson['scenes'][0]['parent_scene_id'], result.lesson['scenes'][1]['parent_scene_id'])
            self.assertNotEqual(result.lesson['scenes'][0]['scene_id'], result.lesson['scenes'][1]['scene_id'])
            for split in result.lesson['scenes']: validate_timeline(split)
            with self.assertRaisesRegex(ValueError, 'order|Narration'):
                validate_sequence(dict(result.lesson, scenes=list(reversed(result.lesson['scenes']))), identify_lesson(plan))

    def test_uncovered_narration_is_rejected_instead_of_cutting_it(self):
        scene = prepare_visual_lesson(fixed_lesson())['scenes'][0]
        audio = AudioSegment(Path('unused.wav'), 60, (NarrationTiming('Only a fragment.', 0, 5),))
        with self.assertRaisesRegex(ValueError, 'complete audio'):
            prepare_audio_lesson({'title': 'Demo', 'scenes': [scene]}, [audio], None)

    def test_legacy_untimed_audio_keeps_every_process_step_after_splitting(self):
        source = prepare_visual_lesson(fixed_lesson())
        process = source['scenes'][2]
        class LegacyTTS:
            def synthesize(self, text, index): return AudioSegment(Path(f'chunk-{index}.wav'), 20)
        result = prepare_audio_lesson(dict(source, scenes=[process]),
                                     [AudioSegment(Path('unused.wav'), 60)], LegacyTTS())
        validate_sequence(result.lesson, dict(source, scenes=[process]))
        steps = [event['parameters']['step_index'] for s in result.lesson['scenes'] for event in s['visual_timeline']]
        self.assertEqual(steps, sorted(steps))
        self.assertEqual(set(steps), {0, 1, 2, 3})

    def test_target_duration_stays_enforced(self):
        validate_measured_duration(240, 4)
        for duration in (90, 300):
            with self.assertRaises(ValueError): validate_measured_duration(duration, 4)
