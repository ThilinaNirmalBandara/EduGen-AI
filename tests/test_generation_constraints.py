"""Full-plan decoding must enforce the constraints that narration-only repair used."""
import math
import re
import unittest
from copy import deepcopy

from src.schemas import (LessonOutline, LessonPlan, english_generation_schema,
                         lesson_generation_schema, narration_word_pattern)
from src.validator import validate_plan


class GenerationConstraintTests(unittest.TestCase):
    def test_full_water_cycle_schema_requires_enough_english_narration(self):
        types = ['bullets'] + ['process']*5 + ['summary']
        schema = lesson_generation_schema(types, 240)
        minimum_words = []
        for item in schema['properties']['scenes']['prefixItems']:
            pattern = item['properties']['narration_text']['pattern']
            minimum, maximum = math.floor(4*130/7), math.ceil(4*150/7)
            minimum_words.append(minimum)
            self.assertIsNotNone(re.fullmatch(pattern, ' '.join(['explanation']*minimum)))
            self.assertIsNotNone(re.fullmatch(pattern, ' '.join(['explanation']*maximum)))
            for words in (minimum-1, maximum+1, 46):
                self.assertIsNone(re.fullmatch(pattern, ' '.join(['explanation']*words)))
            self.assertIsNone(re.fullmatch(pattern, ' '.join(['explanation']*(minimum-1)+['大气'])))
        self.assertGreaterEqual(sum(minimum_words), math.ceil(4*130*0.85))

    def test_display_patterns_preserve_length_limits_and_exclude_chinese(self):
        definitions = lesson_generation_schema()['$defs']
        for kind, field, limit in [('ProcessScene', 'steps', 45), ('WorkedExampleScene', 'steps', 80),
                                    ('BulletScene', 'bullets', 150)]:
            pattern = definitions[kind]['properties'][field]['items']['pattern']
            self.assertIsNotNone(re.fullmatch(pattern, 'a'*limit))
            for value in ('a'*(limit+1), 'Vapor enters 大气', 'air"', r'air\u5927'):
                with self.subTest(kind=kind, value=value):
                    self.assertIsNone(re.fullmatch(pattern, value))
        title = definitions['ProcessScene']['properties']['title']['pattern']
        self.assertIsNotNone(re.fullmatch(title, 'a'*90))
        self.assertIsNone(re.fullmatch(title, 'a'*91))

    def test_duration_enums_reject_millisecond_values_in_every_scene(self):
        schema = lesson_generation_schema(['bullets']+['process']*5+['summary'], 240)
        for definition in schema['$defs'].values():
            properties = definition.get('properties', {})
            if 'duration' in properties:
                allowed = properties['duration']['enum']
                self.assertTrue(all(3 <= value <= 45 for value in allowed))
                self.assertNotIn(12000, allowed)
        for item in schema['properties']['scenes']['prefixItems']:
            duration = item['properties']['duration']
            self.assertTrue(all(duration['minimum'] <= value <= 45 for value in duration['enum']))

    def test_english_outline_constraints_do_not_mutate_pydantic_schema(self):
        original = LessonOutline.model_json_schema()
        before = deepcopy(original)
        constrained = english_generation_schema(original)
        pattern = constrained['$defs']['OutlineSection']['properties']['key_concepts']['items']['pattern']
        self.assertIsNotNone(re.fullmatch(pattern, 'Condensation in the atmosphere'))
        self.assertIsNone(re.fullmatch(pattern, 'Condensation in 大气'))
        self.assertEqual(original, before)

    def test_math_and_legacy_unicode_are_not_treated_as_ascii_prose(self):
        schema = lesson_generation_schema(['equation'], 30)
        self.assertNotIn('pattern', schema['properties']['scenes']['prefixItems'][0]['properties']['equation'])
        validate_plan({'title': 'Legacy Unicode', 'scenes': [
            {'type': 'equation', 'title': 'Update', 'duration': 3, 'equation': 'x₁=x₀-α∇ f(x₀)'}]})
        self.assertNotIn('pattern', LessonPlan.model_json_schema()['$defs']['EquationScene']['properties']['narration_text'])

    def test_narration_word_pattern_excludes_unicode_and_preserves_char_cap(self):
        pattern = narration_word_pattern(74, 86)
        self.assertIsNotNone(re.fullmatch(pattern, ' '.join(["Earth's"]*80)))
        for token in ('大气', r'\alpha', 'water’s', 'word'*100):
            self.assertIsNone(re.fullmatch(pattern, ' '.join(['explanation']*79+[token])))
        for low, high in ((0, 5), (10, 5), (1, 2001)):
            with self.assertRaises(ValueError): narration_word_pattern(low, high)
