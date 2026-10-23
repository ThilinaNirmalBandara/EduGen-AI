"""Inference aborts share the existing bounded validation/repair budget."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.llm import OllamaClient, OllamaError, RecoverableOllamaError
from src.planner import LessonPlanner, LessonPlanGenerationError
from src.schemas import lesson_generation_schema, narration_word_pattern
from test_expansion import OUTLINE, PROMPT, lesson


class SequenceClient:
    model = 'qwen3:4b'

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate_json(self, prompt, schema):
        self.calls.append(prompt)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def repetition():
    return RecoverableOllamaError('prediction aborted, token repeat limit reached (status code: 500)')


class LLMClientTests(unittest.TestCase):
    def test_qwen_uses_non_greedy_non_thinking_with_bounded_context_and_output(self):
        with patch('ollama.chat', return_value={'message': {'content': '{}'}}) as chat:
            self.assertEqual(OllamaClient().generate_json('JSON please', {'type': 'object'}), '{}')
        args = chat.call_args.kwargs
        self.assertFalse(args['think'])
        self.assertEqual(args['model'], 'qwen3:4b')
        self.assertEqual(args['options']['temperature'], 0.7)
        self.assertEqual(args['options']['top_p'], 0.8)
        self.assertEqual(args['options']['num_ctx'], 16384)
        self.assertEqual(args['options']['num_predict'], 8192)

    def test_older_python_client_uses_qwen_soft_switch(self):
        calls = []
        def old_chat(model, messages, format, options):
            calls.append(messages)
            return {'message': {'content': '{}'}}
        with patch('ollama.chat', new=old_chat):
            OllamaClient().generate_json('JSON please', {'type': 'object'})
        self.assertTrue(calls[0][0]['content'].endswith('/no_think'))

    def test_repetition_is_recoverable_without_hidden_client_retries(self):
        from ollama import ResponseError
        with patch('ollama.chat', side_effect=ResponseError('prediction aborted, token repeat limit reached', 500)) as chat:
            with self.assertRaises(RecoverableOllamaError):
                OllamaClient().generate_json('JSON please', {'type': 'object'})
        self.assertEqual(chat.call_count, 1)

    def test_missing_model_is_not_blindly_retried(self):
        from ollama import ResponseError
        with patch('ollama.chat', side_effect=ResponseError('model not found', 404)):
            with self.assertRaises(OllamaError) as context:
                OllamaClient().generate_json('JSON please', {'type': 'object'})
        self.assertNotIsInstance(context.exception, RecoverableOllamaError)

    def test_truncated_and_empty_responses_are_recoverable(self):
        with patch('ollama.chat', return_value={'message': {'content': '{"title":'}, 'done_reason': 'length'}):
            with self.assertRaises(RecoverableOllamaError) as context:
                OllamaClient().generate_json('JSON', {'type': 'object'})
        self.assertEqual(context.exception.partial_response, '{"title":')
        with patch('ollama.chat', return_value={'message': {'content': ''}}):
            with self.assertRaises(RecoverableOllamaError):
                OllamaClient().generate_json('JSON', {'type': 'object'})

    def test_generation_schema_omits_runtime_timelines_and_unbounded_parameter_keys(self):
        schema = lesson_generation_schema(['graph', 'process'], 60)
        self.assertNotIn('VisualEvent', schema['$defs'])
        for item in schema['properties']['scenes']['prefixItems']:
            self.assertNotIn('visual_timeline', item['properties'])
            self.assertFalse(item['properties']['animation_params']['additionalProperties'])
            self.assertIn('initial_x', item['properties']['animation_params']['properties'])

    def test_narration_repair_pattern_enforces_a_spoken_word_budget(self):
        import re
        pattern = narration_word_pattern(5, 8)
        self.assertIsNotNone(re.fullmatch(pattern, 'Each update reduces the loss.'))
        for value in ('Too short.', ' '.join(['word']*9), r'Use \alpha as a rate here.'):
            with self.subTest(value=value):
                self.assertIsNone(re.fullmatch(pattern, value))


class PlannerRecoveryTests(unittest.TestCase):
    def test_only_estimated_timing_is_corrected_without_regenerating_content(self):
        original = lesson(70)
        for scene in original['scenes']:
            scene['duration'] = 16.625
        before = json.dumps(original, sort_keys=True)
        client = SequenceClient([OUTLINE, original])
        result = LessonPlanner(client).plan(PROMPT, 4)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(sum(s['duration'] for s in result['scenes']), 240)
        self.assertEqual(json.dumps(original, sort_keys=True), before)
        for old, new in zip(original['scenes'], result['scenes']):
            self.assertEqual({k:v for k,v in old.items() if k != 'duration'},
                             {k:v for k,v in new.items() if k != 'duration'})

    def test_timing_recalculation_does_not_bypass_narration_depth(self):
        shallow = lesson(10)
        for scene in shallow['scenes']:
            scene['duration'] = 16.625
        client = SequenceClient([OUTLINE, shallow, shallow, shallow])
        with self.assertRaises(LessonPlanGenerationError):
            LessonPlanner(client).plan(PROMPT, 4)

    def test_cached_outline_survives_repetition_and_expansion_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            client = SequenceClient([repetition(), lesson(70)])
            reporter = Mock()
            planner = LessonPlanner(client, cache_dir=Path(directory), reporter=reporter)
            key = planner._cache_key(PROMPT, 4, client.model)
            planner._save_cache('outline', key, OUTLINE)
            result = planner.plan(PROMPT, 4)
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(result['title'], 'Gradient descent')
            self.assertIn('token repeat limit reached', client.calls[1])
            self.assertIn(PROMPT, client.calls[1])
            reporter.generation_retry.assert_called_once()
            self.assertEqual(json.loads((Path(directory) / f'outline_{key}.json').read_text()), OUTLINE)

    def test_inference_and_validation_failures_share_three_attempts(self):
        invalid = lesson(70)
        invalid['scenes'][5]['steps'][0] = 'a display instruction much longer than forty five characters'
        client = SequenceClient([OUTLINE, repetition(), invalid, repetition()])
        with tempfile.TemporaryDirectory() as directory:
            planner = LessonPlanner(client, cache_dir=Path(directory))
            with self.assertRaisesRegex(LessonPlanGenerationError, '3 expansion attempts'):
                planner.plan(PROMPT, 4)
            self.assertEqual(len(client.calls), 4)  # outline + three expansion attempts
            self.assertFalse(list(Path(directory).glob('plan_*.json')))
            self.assertTrue(list(Path(directory).glob('outline_*.json')))
            self.assertTrue(list(Path(directory).glob('expansion_draft_*.json')))

    def test_outline_generation_recovers_without_losing_original_request(self):
        client = SequenceClient([repetition(), OUTLINE, lesson(70)])
        LessonPlanner(client).plan(PROMPT, 4)
        self.assertIn(PROMPT, client.calls[1])
        self.assertEqual(len(client.calls), 3)

    def test_duration_revision_retries_and_preserves_input(self):
        original = lesson(70)
        before = json.dumps(original, sort_keys=True)
        client = SequenceClient([repetition(), repetition(), repetition()])
        with self.assertRaisesRegex(LessonPlanGenerationError, 'original valid lesson was preserved'):
            LessonPlanner(client).revise_for_duration(original, PROMPT, 4, 'too long')
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(json.dumps(original, sort_keys=True), before)

    def test_valid_draft_is_kept_when_inference_aborts_during_narration_repair(self):
        from test_expansion import narration_patch
        client = SequenceClient([OUTLINE, lesson(39), repetition(), narration_patch()])
        result = LessonPlanner(client).plan(PROMPT, 4)
        self.assertEqual(result['scenes'][5]['steps'], lesson(39)['scenes'][5]['steps'])
        self.assertIn('NARRATION-ONLY REPAIR', client.calls[-1])
