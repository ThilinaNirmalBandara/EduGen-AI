import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.planner import LessonPlanGenerationError, LessonPlanner
from src.duration import DurationConstraintError, validate_measured_duration


class FakeRevisionClient:
    model = "qwen3:4b"

    def __init__(self, responses):
        self.responses = responses
        self.prompts = []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        return self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]


def original_plan():
    return {"title": "Gradient descent", "scenes": [{
        "type": "process", "title": "Learning loop",
        "steps": ["Measure the loss", "Update weights"], "duration": 8,
        "narration_text": "The optimizer measures error and updates each weight using the gradient.",
        "visual_type": "process",
    }]}


class DurationRevisionTests(unittest.TestCase):
    def test_invalid_process_steps_are_repaired(self):
        invalid = {"title": "Gradient descent", "scenes": [{
            "type": "process", "title": "Learning loop",
            "steps": ["Calculate the gradient of the loss for every parameter", "Update weights"],
            "duration": 8, "narration_text": "The gradient tells us how to update every parameter.",
            "visual_type": "process",
        }]}
        repaired = original_plan()
        client = FakeRevisionClient([invalid, repaired])
        with patch("src.planner.validate_lesson_quality"):
            result = LessonPlanner(client).revise_for_duration(
                original_plan(), "Explain gradient descent", 1, "measured narration is too long"
            )
        self.assertEqual(result["scenes"][0]["steps"][0], "Measure the loss")
        self.assertEqual(len(client.prompts), 2)
        self.assertIn("each step must be 1–45 characters", client.prompts[1])
        self.assertIn("Previous generated JSON", client.prompts[1])

    def test_retry_exhaustion_preserves_original_plan(self):
        invalid = {"title": "Gradient descent", "scenes": [{
            "type": "process", "title": "Learning loop",
            "steps": ["This display step is intentionally much too long to validate", "Update weights"],
            "duration": 8, "narration_text": "The gradient updates parameters.",
        }]}
        original = original_plan()
        client = FakeRevisionClient([invalid, invalid, invalid])
        with patch("src.planner.validate_lesson_quality"):
            with self.assertRaises(LessonPlanGenerationError) as context:
                LessonPlanner(client).revise_for_duration(
                    original, "Explain gradient descent", 1, "too long"
                )
        self.assertEqual(len(client.prompts), 3)
        self.assertIn("original valid lesson was preserved", str(context.exception))
        self.assertEqual(original["scenes"][0]["steps"][0], "Measure the loss")

    def test_successful_revision_is_cached_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeRevisionClient([original_plan()])
            planner = LessonPlanner(client, cache_dir=Path(directory))
            with patch("src.planner.validate_lesson_quality"):
                planner.revise_for_duration(original_plan(), "Explain gradient descent", 1, "too long")
            second_client = FakeRevisionClient([])
            second = LessonPlanner(second_client, cache_dir=Path(directory))
            with patch("src.planner.validate_lesson_quality"):
                second.revise_for_duration(original_plan(), "Explain gradient descent", 1, "too long")
            self.assertEqual(second_client.prompts, [])
            self.assertTrue(list(Path(directory).glob("revised_plan_*.json")))

    def test_measured_revised_narration_is_checked_again(self):
        with self.assertRaises(DurationConstraintError):
            validate_measured_duration(20, target_minutes=1, tolerance=0.10)
        validate_measured_duration(60, target_minutes=1, tolerance=0.10)


if __name__ == "__main__":
    unittest.main()
