import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from src.schemas import VisualEvent
from src.tts import AudioSegment, NarrationTiming
from src.validator import validate_plan
from src.video.layout import LayoutBox, VisualQualityError, validate_layout
from src.video.mathematics import MathRenderingError, check_latex_dependencies, gradient_updates, polynomial_function, sanitize_math
from src.video.timeline import bind_audio_timeline, narration_units, prepare_visual_lesson, validate_timeline


class MathematicsTests(unittest.TestCase):
    def test_common_expressions_and_unicode_are_normalized(self):
        expressions = [r'\theta_{t+1}=\theta_t-\alpha\nabla_\theta L(\theta_t)',
                       r'\frac{\partial L}{\partial w_i}', r'x_{n+1}=x_n-\eta f\prime(x_n)',
                       'theta_{t+1}=theta_t - α ∇ L(θ)', 'f(x)=x²', r'\sqrt{x^2+y^2}']
        # Use an ordinary apostrophe for a prime; \prime is also common math LaTeX.
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertTrue(sanitize_math(expression))
        self.assertEqual(sanitize_math('$f(x)=x²$'), 'f(x)=x^{2}')
        self.assertIn(r'\theta', sanitize_math('theta_{t+1}'))
        self.assertEqual(sanitize_math(r'\\alpha'), r'\alpha')

    def test_unsafe_or_invalid_latex_is_rejected(self):
        for value in (r'\input{/etc/passwd}', r'\write18{touch file}', r'\def\x{a}',
                      r'\csname input\endcsname{file}', r'\includegraphics{file}',
                      r'\frac{x}{y', '^^5cinput{file}', 'x%comment', 'x$y'):
            with self.subTest(value=value), self.assertRaises(MathRenderingError):
                sanitize_math(value)

    def test_missing_latex_is_actionable(self):
        with patch('src.video.mathematics.shutil.which', return_value=None):
            with self.assertRaisesRegex(MathRenderingError, 'sudo apt install'):
                check_latex_dependencies()

    def test_numerical_updates_are_computed(self):
        states = gradient_updates(iterations=3)
        self.assertEqual(states[0].gradient, 6)
        self.assertAlmostEqual(states[0].next_x, 1.8)
        self.assertAlmostEqual(states[0].next_loss, 3.24)
        self.assertAlmostEqual(states[1].next_x, 1.08)
        self.assertAlmostEqual(states[1].next_loss, 1.1664)
        self.assertTrue(all(state.next_loss < state.loss for state in states))
        self.assertAlmostEqual(gradient_updates(offset=1)[0].x, states[0].next_x)

    def test_numerical_demo_rejects_invalid_parameters(self):
        for rate in (0, 1, float('nan')):
            with self.assertRaises(ValueError):
                gradient_updates(learning_rate=rate)

    def test_polynomial_graphs_compute_values_without_executing_code(self):
        self.assertEqual(polynomial_function('y=(x-2)^2')(3), 1)
        self.assertEqual(polynomial_function('y=x²+1')(2), 5)
        for expression in ('__import__("os").system("id")', 'x.__class__', 'x**10000', '[x for x in range(3)]'):
            with self.subTest(expression=expression), self.assertRaises(MathRenderingError):
                polynomial_function(expression)


class TimelineTests(unittest.TestCase):
    def test_duration_revision_rebuilds_stale_cue_text_before_tts(self):
        plan = {'title': 'A process', 'scenes': [
            {'type': 'process', 'title': 'Updated explanation', 'duration': 10,
             'steps': ['Calculate', 'Compare'], 'narration_text': 'A revised concise explanation.',
             'visual_timeline': [VisualEvent(narration_text='The previous longer explanation.').model_dump()]}]}
        updated = prepare_visual_lesson(plan)
        cues = updated['scenes'][0]['visual_timeline']
        self.assertEqual(' '.join(cue['narration_text'] for cue in cues), 'A revised concise explanation.')
        self.assertEqual(plan['scenes'][0]['visual_timeline'][0]['narration_text'], 'The previous longer explanation.')
    def test_short_legacy_narration_covers_the_minimum_scene_duration(self):
        scene = {'type': 'title', 'title': 'Welcome', 'duration': 3}
        audio = AudioSegment(Path('short.wav'), 1, (NarrationTiming('Welcome.', 0, 1),))
        result = bind_audio_timeline(scene, audio)
        validate_timeline(result)
        self.assertEqual(result['visual_timeline'][-1]['action'], 'highlight')
        self.assertEqual(result['visual_timeline'][-1]['animation_duration'], 2)
    def test_narration_partition_preserves_every_word(self):
        text = 'First measure the gradient. Then update the parameters; check the loss. ' + ' '.join(['word']*40)
        units = narration_units(text)
        self.assertEqual(' '.join(units), text)
        self.assertTrue(all(len(unit.split()) <= 16 for unit in units))

    def test_measured_audio_drives_event_times_and_preserves_actions(self):
        scene = {'type': 'graph', 'title': 'Loss', 'duration': 8,
                 'visual_timeline': [VisualEvent(action='gradient').model_dump(), VisualEvent(action='update').model_dump()]}
        audio = AudioSegment(Path('audio.wav'), 8, (
            NarrationTiming('Slope is six.', 0, 3, 0), NarrationTiming('Update to one point eight.', 3, 5, 1)))
        result = bind_audio_timeline(scene, audio)
        self.assertEqual(result['visual_timeline'][1]['start_time'], 3)
        self.assertEqual(result['visual_timeline'][1]['animation_duration'], 5)
        self.assertEqual(result['visual_timeline'][1]['action'], 'update')
        validate_timeline(result)

    def test_static_gaps_missing_coverage_and_overlaps_are_rejected(self):
        for events in ([], [{'start_time': 0, 'animation_duration': 12}],
                       [{'start_time': 0, 'animation_duration': 2}, {'start_time': 4, 'animation_duration': 4}],
                       [{'start_time': 0, 'animation_duration': 4}, {'start_time': 3, 'animation_duration': 5}]):
            with self.assertRaises(ValueError):
                validate_timeline({'title': 'Bad', 'duration': 8, 'visual_timeline': events})

    def test_demo_upgrade_preserves_original_and_matches_computed_narration(self):
        original = {'title': 'Gradient descent', 'scenes': [
            {'type': 'worked_example', 'title': 'Worked example', 'duration': 30,
             'equation': 'f(x)=x^2', 'steps': ['Find slope', 'Update x'], 'narration_text': 'Old numerical explanation.'}]}
        before = deepcopy(original)
        upgraded = prepare_visual_lesson(original)
        self.assertEqual(original, before)
        self.assertIn('1.8', upgraded['scenes'][0]['narration_text'])
        self.assertIn('3.24', upgraded['scenes'][0]['narration_text'])
        validate_plan(upgraded)


class LayoutTests(unittest.TestCase):
    def test_bounds_font_size_and_overlap(self):
        good = LayoutBox('left', -5, -1, -1, 1, 32)
        validate_layout([good, LayoutBox('right', 1, 5, -1, 1, 32)])
        for boxes in ([LayoutBox('small', 0, 1, 0, 1, 12)],
                      [LayoutBox('outside', -8, 1, 0, 1, 32)],
                      [good, LayoutBox('overlap', -2, 2, -1, 1, 32)]):
            with self.assertRaises(VisualQualityError):
                validate_layout(boxes)
