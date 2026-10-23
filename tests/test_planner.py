import unittest
import tempfile
from pathlib import Path

from src.planner import LessonPlanGenerationError, LessonPlanner
from src.validator import LessonValidationError, validate_plan


class FakeOllama:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.prompts = []
        self.schema = None

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        self.schema = schema
        return self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]


class PlannerTests(unittest.TestCase):
    def test_two_stage_planner_expands_outline(self):
        outline = {
            "title": "Gradient Descent", "audience": "beginner",
            "learning_goals": ["Understand loss", "Apply updates"],
            "sections": [
                {"section_id": "intro", "title": "Introduction", "learning_objective": "Orient the learner", "key_concepts": ["loss"], "visual_type": "concepts"},
                {"section_id": "math", "title": "Update equation", "learning_objective": "Use the rule", "key_concepts": ["gradient"], "visual_type": "equation"},
                {"section_id": "graph", "title": "Moving downhill", "learning_objective": "See optimization", "key_concepts": ["minimum"], "visual_type": "graph"},
            ],
        }
        narration = "Gradient descent minimizes a loss function by taking small steps. " * 30
        expanded = {"title": "Gradient Descent", "scenes": [
            {"type": "title", "title": "Gradient Descent", "duration": 20, "narration_text": narration, "visual_type": "title"},
            {"type": "equation", "title": "The update rule", "equation": "w_new = w - η∇L(w)", "duration": 35, "narration_text": narration, "visual_type": "equation"},
            {"type": "graph", "title": "Moving toward the minimum", "duration": 35, "narration_text": narration, "visual_type": "graph"},
            {"type": "worked_example", "title": "A numerical update", "equation": "w = 2 - 0.1(6) = 1.4", "steps": ["Start at w = 2", "Gradient is 6", "Move by 0.6", "New weight is 1.4"], "duration": 35, "narration_text": narration, "visual_type": "worked_example"},
            {"type": "summary", "title": "Summary", "bullets": ["Repeat gradient updates"], "duration": 10, "narration_text": narration},
        ]}
        client = FakeOllama([outline, expanded])
        plan = LessonPlanner(client).plan("Explain gradient descent", duration_minutes=2)
        self.assertEqual(len(client.prompts), 2)
        self.assertIn("Outline from Stage A", client.prompts[1])
        self.assertEqual(plan["scenes"][2]["type"], "graph")

    def test_validated_plan_is_reused_from_cache(self):
        outline = {
            "title": "Gradient Descent", "audience": "beginner",
            "learning_goals": ["Understand loss", "Apply updates"],
            "sections": [
                {"section_id": "a", "title": "Intro", "learning_objective": "Orient", "key_concepts": ["loss"], "visual_type": "concepts"},
                {"section_id": "b", "title": "Equation", "learning_objective": "Apply", "key_concepts": ["gradient"], "visual_type": "equation"},
                {"section_id": "c", "title": "Graph", "learning_objective": "See", "key_concepts": ["minimum"], "visual_type": "graph"},
            ],
        }
        narration = "Gradient descent updates a parameter using the slope of the loss. " * 30
        expanded = {"title": "Gradient Descent", "scenes": [
            {"type": "title", "title": "Intro", "duration": 40, "narration_text": narration},
            {"type": "equation", "title": "Equation", "equation": "w = w - ηg", "duration": 40, "narration_text": narration},
            {"type": "graph", "title": "Graph", "duration": 40, "narration_text": narration},
            {"type": "summary", "title": "Summary", "bullets": ["Monitor the loss"], "duration": 10, "narration_text": narration},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            first_client = FakeOllama([outline, expanded])
            LessonPlanner(first_client, cache_dir=Path(directory)).plan("Explain gradient descent", duration_minutes=2)
            second_client = FakeOllama({"bad": "this should not be called"})
            LessonPlanner(second_client, cache_dir=Path(directory)).plan("Explain gradient descent", duration_minutes=2)
        self.assertEqual(len(first_client.prompts), 2)
        self.assertEqual(len(second_client.prompts), 0)

    def test_planner_parses_and_validates_structured_json(self):
        client = FakeOllama(
            '{"title":"Fractions","scenes":[{"type":"title",'
            '"title":"Fractions","subtitle":"Parts of a whole","duration":3}]}'
        )
        plan = LessonPlanner(client).plan("Teach the basics of fractions")
        self.assertEqual(plan["title"], "Fractions")
        self.assertIn("properties", client.schema)
        self.assertIn("fractions", client.prompts[0].lower())

    def test_planner_includes_duration_and_narration_requirements(self):
        client = FakeOllama({"title": "Topic", "scenes": [{
            "type": "title", "title": "Topic", "duration": 3,
            "narration_text": "A detailed explanation for the learner.",
        }]})
        LessonPlanner(client).plan("Teach a topic", duration_minutes=4)
        self.assertIn("approximately 4 minutes", client.prompts[0])
        self.assertIn("narration_text", client.prompts[0])

    def test_invalid_process_step_length_is_repaired(self):
        invalid = {"title": "Gradient descent", "scenes": [{
            "type": "process", "title": "How learning works",
            "steps": ["Calculate the gradient of the loss function for every weight", "Update weights"],
            "duration": 5,
        }]}
        repaired = {"title": "Gradient descent", "scenes": [{
            "type": "process", "title": "How learning works",
            "steps": ["Calculate loss gradient", "Update weights"], "duration": 5,
        }]}
        client = FakeOllama([invalid, repaired])
        plan = LessonPlanner(client).plan("Explain gradient descent")
        self.assertEqual(plan["scenes"][0]["steps"][0], "Calculate loss gradient")
        self.assertEqual(len(client.prompts), 2)
        self.assertIn("Previous generated JSON", client.prompts[1])
        self.assertIn("45", client.prompts[1])

    def test_retry_exhaustion_reports_remaining_failures(self):
        invalid = {"title": "Gradient descent", "scenes": [{
            "type": "process", "title": "Learning", "steps": [
                "This process step remains much too long for display", "Update weights"
            ], "duration": 5,
        }]}
        client = FakeOllama([invalid, invalid, invalid])
        with self.assertRaises(LessonPlanGenerationError) as context:
            LessonPlanner(client).plan("Explain gradient descent")
        self.assertEqual(len(client.prompts), 3)
        self.assertIn("after 3 attempts", str(context.exception))
        self.assertIn("validation failures", str(context.exception))

    def test_planner_rejects_model_fields_outside_contract(self):
        client = FakeOllama(
            {"title": "Unsafe", "scenes": [{"type": "title", "title": "X", "duration": 3,
                                               "python": "print('bad')"}]}
        )
        with self.assertRaises(LessonValidationError):
            LessonPlanner(client).plan("anything")

    def test_validator_rejects_unknown_scene_type(self):
        with self.assertRaises(LessonValidationError):
            validate_plan({"title": "Bad", "scenes": [{"type": "python_exec", "title": "X", "duration": 3}]})


if __name__ == "__main__":
    unittest.main()
