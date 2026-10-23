"""An old narration-only cache must not freeze newly invalid educational content."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from test_expansion import Client, OUTLINE, PROMPT, lesson, narration_patch
from src.content import validate_content
from src.planner import LessonPlanner, LessonPlanGenerationError
from src.video.mathematics import (MathRenderingError, numerical_equalities,
    sanitize_math, validate_calculation, validate_math_in_text)


class RepairMigrationTests(unittest.TestCase):
    def test_stale_frozen_foreign_text_and_missing_summary_trigger_full_repair(self):
        bad = lesson()
        bad['scenes'][5]['steps'][1] = 'Move in the 向方 direction'
        bad['scenes'][-1]['type'] = 'bullets'
        with tempfile.TemporaryDirectory() as directory:
            client = Client([lesson(70)])
            planner = LessonPlanner(client, cache_dir=Path(directory))
            key = planner._cache_key(PROMPT, 4, client.model)
            planner._save_cache('outline', key, OUTLINE)
            planner._save_cache('expansion_draft', key, {
                'version': 1, 'previous': json.dumps(narration_patch()),
                'failure': 'Old rules: narration is too short', 'narration_base': bad})
            result = planner.plan(PROMPT, 4)
            self.assertEqual(len(client.calls), 1)
            prompt, schema = client.calls[0]
            self.assertEqual(schema['title'], 'LessonPlan')
            self.assertNotIn('NARRATION-ONLY REPAIR:', prompt)
            self.assertIn('non-English', prompt)
            self.assertIn('final scene', prompt)
            self.assertIn('Move in the 向方 direction', prompt)
            self.assertEqual(result['scenes'][-1]['type'], 'summary')

    def test_stale_structurally_invalid_base_is_not_frozen(self):
        bad = lesson()
        bad['scenes'][5]['steps'][0] = 'overlong display instruction ' * 3
        with tempfile.TemporaryDirectory() as directory:
            client = Client([lesson(70)])
            planner = LessonPlanner(client, cache_dir=Path(directory))
            key = planner._cache_key(PROMPT, 4, client.model)
            planner._save_cache('outline', key, OUTLINE)
            planner._save_cache('expansion_draft', key, {
                'version': 1, 'previous': narration_patch(), 'failure': 'old', 'narration_base': bad})
            planner.plan(PROMPT, 4)
            self.assertEqual(client.calls[0][1]['title'], 'LessonPlan')
            self.assertIn('1–45', client.calls[0][0])

    def test_non_narration_quality_failure_releases_the_frozen_base(self):
        bad_patch = narration_patch()
        bad_patch['scenes'][0]['narration_text'] += ' 向方'
        client = Client([OUTLINE, lesson(), bad_patch, lesson(70)])
        LessonPlanner(client).plan(PROMPT, 4)
        self.assertEqual([schema['title'] for _, schema in client.calls],
                         ['LessonOutline', 'LessonPlan', 'NarrationRevision', 'LessonPlan'])
        self.assertIn('non-English', client.calls[-1][0])

    def test_repaired_migration_still_has_three_attempts_and_keeps_old_cache(self):
        bad = lesson(70)
        bad['scenes'][5]['steps'][1] += ' 向方'
        with tempfile.TemporaryDirectory() as directory:
            client = Client([bad, bad, bad])
            planner = LessonPlanner(client, cache_dir=Path(directory))
            key = planner._cache_key(PROMPT, 4, client.model)
            planner._save_cache('outline', key, OUTLINE)
            planner._save_cache('plan', key, bad)
            planner._save_cache('expansion_draft', key, {
                'version': 1, 'previous': narration_patch(), 'failure': 'old', 'narration_base': bad})
            with self.assertRaisesRegex(LessonPlanGenerationError, '3 expansion attempts'):
                planner.plan(PROMPT, 4)
            self.assertEqual(len(client.calls), 3)
            self.assertTrue(all(schema['title'] == 'LessonPlan' for _, schema in client.calls))
            self.assertEqual(planner._load_cache('plan', key), bad)

    def test_now_valid_checkpoint_is_reused_without_ollama(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client([])
            planner = LessonPlanner(client, cache_dir=Path(directory))
            key = planner._cache_key(PROMPT, 4, client.model)
            planner._save_cache('outline', key, OUTLINE)
            planner._save_cache('expansion_draft', key, {'version': 1,
                'previous': narration_patch(), 'failure': 'old validator', 'narration_base': lesson()})
            result = planner.plan(PROMPT, 4)
            self.assertEqual(client.calls, [])
            self.assertEqual(len(result['scenes'][0]['narration_text'].split()), 70)


class EmbeddedMathematicsTests(unittest.TestCase):
    def test_unicode_subscript_normalization_is_lossless(self):
        self.assertEqual(sanitize_math('x₀ = 3'), 'x_{0} = 3')
        self.assertEqual(sanitize_math('x₁₂=x₀-α∇ f(x₀)'), r'x_{12}=x_{0}-\alpha \nabla  f(x_{0})')

    def test_valid_equations_embedded_in_prose_are_not_rejected_as_tex_operands(self):
        for text in ('Start at an initial point (e.g., x₀ = 3)', 'Start with x₀ = 3',
                     "f'(x) = 2x, so at x₀=3, gradient = 6", 'x₁ = 3 - 0.2*6 = 1.8',
                     'Repeat for x₂ = 1.2 - 0.2*2.4 = 0.72'):
            with self.subTest(text=text): validate_math_in_text(text)

    def test_narration_checks_the_full_expression_not_a_numeric_suffix(self):
        text = 'Then 1.2 - 0.2*2.4 = 0.72. Compare the result.'
        self.assertEqual(numerical_equalities(text), ['1.2 - 0.2*2.4 = 0.72'])
        for expression in numerical_equalities(text): validate_calculation(expression)
        for text in ('3 - 0.2*6 = 1.2', '1.2 - 0.2*2.4 = 0.96'):
            with self.subTest(text=text), self.assertRaises(MathRenderingError):
                validate_math_in_text(text)

    def test_symbols_are_not_treated_as_constant_arithmetic_suffixes(self):
        for text in ('f(x)=x^2 = 9.', 'Gradient is 2*x = 6.'):
            self.assertEqual(numerical_equalities(text), [])

    def test_unknown_latex_and_wrong_fractions_are_still_rejected(self):
        for text in (r'\input{/etc/passwd}', r'Fraction: \frac{1}{2}=0.4', 'x₁=x₀-'):
            with self.subTest(text=text), self.assertRaises(MathRenderingError):
                validate_math_in_text(text)
