import tempfile
import unittest
from pathlib import Path

from src.duration import prepare_audio_lesson, validate_measured_duration
from src.tts import AudioSegment


class FakeSplitTTS:
    def synthesize(self, text, index):
        # Initial scene is overlong; newly synthesized natural chunks fit.
        return AudioSegment(Path(f"audio-{index}.wav"), 53.4 if index == 1 else 20.0)


class DurationTests(unittest.TestCase):
    def test_overlong_scene_is_split_at_sentence_boundaries(self):
        lesson = {"title": "Gradient descent", "scenes": [{
            "type": "graph", "title": "Moving downhill", "duration": 10,
            "narration_text": (
                "Start at the right side of the curve. Follow the gradient direction. "
                "Take a smaller step near the minimum. Observe the loss decrease."
            ),
        }]}
        original_audio = [AudioSegment(Path("original.wav"), 53.4)]
        prepared = prepare_audio_lesson(lesson, original_audio, FakeSplitTTS())
        self.assertGreater(len(prepared.lesson["scenes"]), 1)
        self.assertEqual(len(prepared.audio_segments), len(prepared.lesson["scenes"]))
        self.assertTrue(all(segment.duration <= 44.5 for segment in prepared.audio_segments))
        self.assertNotEqual(
            prepared.lesson["scenes"][0]["animation_params"],
            prepared.lesson["scenes"][1]["animation_params"],
        )
        self.assertEqual(prepared.split_count, len(prepared.lesson["scenes"]) - 1)

    def test_measured_duration_tolerance_is_enforced(self):
        validate_measured_duration(240, 4, 0.15)
        with self.assertRaises(ValueError):
            validate_measured_duration(200, 4, 0.15)


if __name__ == "__main__":
    unittest.main()
