import unittest

from src.quality import LessonQualityError, validate_audio_timing, validate_lesson_quality


class QualityTests(unittest.TestCase):
    def test_shallow_static_plan_is_rejected(self):
        plan = {
            "scenes": [
                {"type": "title", "title": "Topic", "duration": 30, "narration_text": "Short."},
                {"type": "bullets", "title": "Facts", "bullets": ["One"], "duration": 30, "narration_text": "Short."},
                {"type": "summary", "title": "End", "bullets": ["Done"], "duration": 30, "narration_text": "Short."},
            ]
        }
        with self.assertRaises(LessonQualityError) as context:
            validate_lesson_quality(plan, 3, prompt="Explain gradient descent")
        self.assertIn("too many scenes are static", str(context.exception))

    def test_measured_audio_must_be_close_to_target(self):
        with self.assertRaises(LessonQualityError):
            validate_audio_timing([10, 10], 3)
        validate_audio_timing([55, 55, 55], 3)


if __name__ == "__main__":
    unittest.main()
