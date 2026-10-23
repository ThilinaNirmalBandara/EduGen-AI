"""Regression coverage for short, incomplete four-minute lesson expansions."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.planner import LessonPlanner, LessonPlanGenerationError
from src.quality import LessonQualityError, validate_lesson_quality
from src.schemas import lesson_generation_schema
from src.validator import validate_plan, validate_outline


PROMPT = "Explain gradient descent with mathematical derivation and a worked example"
OUTLINE = {
    "title": "Gradient descent", "audience": "Beginner",
    "learning_goals": ["Understand gradients", "Calculate an update"],
    "sections": [
        {"section_id": "loss", "title": "Loss curve", "learning_objective": "Interpret loss",
         "key_concepts": ["minimum"], "visual_type": "graph"},
        {"section_id": "rule", "title": "Derivation", "learning_objective": "Derive the rule",
         "key_concepts": ["derivative"], "visual_type": "equation"},
        {"section_id": "example", "title": "Example", "learning_objective": "Calculate an update",
         "key_concepts": ["learning rate"], "visual_type": "worked_example"},
    ],
}


def lesson(words_per_scene=39):
    scenes = [
        {"type": "graph", "title": "Loss curve", "animation_params": {"start_x": 3}},
        {"type": "equation", "title": "Derivative", "equation": "L'(w) = 2w"},
        {"type": "equation", "title": "Update rule", "equation": "w_new = w - eta * L'(w)"},
        {"type": "worked_example", "title": "Worked update", "equation": "2 - 0.1 * 4 = 1.6",
         "steps": ["Start at w = 2", "Compute slope 4", "Subtract 0.4"]},
        {"type": "graph", "title": "Compare loss"},
        {"type": "process", "title": "Repeat the update",
         "steps": ["Calculate slope", "Update weight", "Measure new loss"]},
        {"type": "comparison", "title": "Learning rates", "left": "Small rate",
         "right": "Large rate", "relationship": "Stability versus overshoot"},
        {"type": "summary", "title": "What we learned", "bullets": ["Follow the negative gradient"]},
    ]
    for scene in scenes:
        scene.update(duration=30, narration_text=" ".join(["explanation"] * words_per_scene))
    return {"title": "Gradient descent", "scenes": scenes}


def narration_patch(words=70):
    return {"scenes": [{"scene_index": index, "narration_text": " ".join(["reasoning"] * words)}
                       for index in range(1, 9)]}


class Client:
    model = "qwen3:4b"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate_json(self, prompt, schema):
        self.calls.append((prompt, schema))
        return next(self.responses)


class ExpansionTests(unittest.TestCase):
    def test_generation_schema_requires_narration_and_display_bounds(self):
        definitions = lesson_generation_schema()["$defs"]
        for name in ("TitleScene", "ProcessScene", "GraphScene", "EquationScene",
                     "WorkedExampleScene", "BulletScene", "ComparisonScene"):
            self.assertIn("narration_text", definitions[name]["required"])
            self.assertEqual(definitions[name]["properties"]["narration_text"]["minLength"], 1)
        self.assertEqual(definitions["ProcessScene"]["properties"]["steps"]["items"]["maxLength"], 45)
        self.assertEqual(definitions["WorkedExampleScene"]["properties"]["steps"]["items"]["maxLength"], 80)
        self.assertEqual(definitions["BulletScene"]["properties"]["bullets"]["items"]["maxLength"], 150)
        validate_plan({"title": "Legacy", "scenes": [{"type": "title", "title": "Intro", "duration": 3}]})

    def test_expansion_schema_enforces_the_outline_visual_types_by_position(self):
        types = LessonPlanner._outline_scene_types(validate_outline(OUTLINE), PROMPT, 4)
        schema = lesson_generation_schema(types, 240)
        scenes = schema["properties"]["scenes"]
        self.assertNotIn("items", scenes)
        self.assertEqual(scenes["minItems"], len(types))
        self.assertEqual(scenes["maxItems"], len(types))
        self.assertEqual([item["properties"]["type"]["const"] for item in scenes["prefixItems"]], types)
        self.assertTrue({"equation", "graph", "worked_example"}.issubset(types))
        self.assertGreaterEqual(len(types), 6)
        self.assertGreaterEqual(sum(item["properties"]["duration"]["minimum"]
                                    for item in scenes["prefixItems"]), 204)

    def test_mathematical_outline_missing_explanatory_templates_is_completed(self):
        static = deepcopy(OUTLINE)
        for section in static["sections"]:
            section["visual_type"] = "concepts"
        types = LessonPlanner._outline_scene_types(validate_outline(static), PROMPT, 4)
        self.assertTrue({"equation", "graph", "worked_example"}.issubset(types))

    def test_impossible_scene_budget_is_reported_before_expansion(self):
        with self.assertRaisesRegex(LessonPlanGenerationError, "shorter duration"):
            LessonPlanner._outline_scene_types(validate_outline(OUTLINE), PROMPT, 30)

    def test_short_narration_repair_preserves_all_visuals_and_context(self):
        short = lesson()
        client = Client([OUTLINE, short, narration_patch()])
        result = LessonPlanner(client).plan(PROMPT, 4)
        self.assertEqual(len(client.calls), 3)
        repair_prompt, repair_schema = client.calls[-1]
        self.assertIn(PROMPT, repair_prompt)
        self.assertIn("Outline from Stage A", repair_prompt)
        self.assertIn("at least 442 words", repair_prompt)
        self.assertIn("NARRATION-ONLY REPAIR", repair_prompt)
        self.assertEqual(repair_schema["title"], "NarrationRevision")
        for old, new in zip(short["scenes"], result["scenes"]):
            self.assertEqual({k: v for k, v in old.items() if k != "narration_text"},
                             {k: v for k, v in new.items() if k != "narration_text"})
        validate_lesson_quality(result, 4, prompt=PROMPT)

    def test_missing_narration_is_added_without_changing_valid_visuals(self):
        draft = lesson()
        for scene in draft["scenes"]:
            scene.pop("narration_text")
        client = Client([OUTLINE, draft, narration_patch()])
        result = LessonPlanner(client).plan(PROMPT, 4)
        self.assertTrue(all(scene["narration_text"] for scene in result["scenes"]))
        self.assertIn("NARRATION-ONLY REPAIR", client.calls[-1][0])

    def test_resume_after_exhaustion_uses_cached_outline_and_latest_draft(self):
        invalid = deepcopy(lesson())
        invalid["scenes"][5]["steps"][0] = "Overlong process display instruction " * 3
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            first = Client([OUTLINE, invalid, invalid, lesson()])
            with self.assertRaisesRegex(LessonPlanGenerationError, "resume repair"):
                LessonPlanner(first, cache_dir=cache).plan(PROMPT, 4)
            self.assertFalse(list(cache.glob("plan_*.json")))
            checkpoint = json.loads(next(cache.glob("expansion_draft_*.json")).read_text())
            self.assertIn("narration_base", checkpoint)
            second = Client([narration_patch()])
            result = LessonPlanner(second, cache_dir=cache).plan(PROMPT, 4)
            self.assertEqual(len(second.calls), 1)
            validate_lesson_quality(result, 4, prompt=PROMPT)
            cached_client = Client([])
            LessonPlanner(cached_client, cache_dir=cache).plan(PROMPT, 4)
            self.assertEqual(cached_client.calls, [])

    def test_incomplete_narration_patch_retried_and_complete_lesson_revalidated(self):
        incomplete = narration_patch()
        incomplete["scenes"].pop()
        client = Client([OUTLINE, lesson(), incomplete, narration_patch()])
        result = LessonPlanner(client).plan(PROMPT, 4)
        self.assertIn("every scene index exactly once", client.calls[-1][0])
        validate_lesson_quality(result, 4, prompt=PROMPT)

    def test_short_repair_still_fails_quality_checks(self):
        client = Client([OUTLINE, lesson(), narration_patch(10), narration_patch(10)])
        with self.assertRaisesRegex(LessonPlanGenerationError, "narration has 80 words"):
            LessonPlanner(client).plan(PROMPT, 4)

    def test_force_bypasses_outline_plan_and_draft_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            LessonPlanner(Client([OUTLINE, lesson(70)]), cache_dir=cache).plan(PROMPT, 4)
            forced = Client([OUTLINE, lesson(70)])
            LessonPlanner(forced, cache_dir=cache).plan(PROMPT, 4, force=True)
            self.assertEqual(len(forced.calls), 2)

    def test_equivalent_numeric_inputs_share_cache_keys(self):
        integer_inputs = LessonPlanner(Client([]), generation_settings={"speed": 1})
        float_inputs = LessonPlanner(Client([]), generation_settings={"speed": 1.0})
        self.assertEqual(integer_inputs._cache_key(PROMPT, 4, "qwen3:4b"),
                         float_inputs._cache_key(PROMPT, 4.0, "qwen3:4b"))
        self.assertEqual(integer_inputs._revision_cache_key(lesson(), PROMPT, 4, "long", "qwen3:4b"),
                         float_inputs._revision_cache_key(lesson(), PROMPT, 4.0, "long", "qwen3:4b"))

    def test_visual_metadata_cannot_bypass_real_template_quality_checks(self):
        static = lesson(70)
        for scene in static["scenes"]:
            scene.update(type="bullets", visual_type="graph", bullets=["Short label"])
        with self.assertRaisesRegex(LessonQualityError, "too many scenes are static"):
            validate_lesson_quality(static, 4, prompt=PROMPT)
