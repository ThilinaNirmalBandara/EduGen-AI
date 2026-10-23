import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from src.tts import AudioSegment, KokoroTTS, narration_for_scene


class FakeReporter:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        def record(*args):
            self.events.append((name, args))
        return record


class FakeTTS(KokoroTTS):
    def synthesize(self, text, index):
        return AudioSegment(Path(f"scene-{index}.wav"), 4.25)


class FakePipeline:
    sample_rate = 100

    def __init__(self):
        self.calls = 0

    def __call__(self, text, voice, speed):
        self.calls += 1
        return iter([(None, None, np.zeros(100, dtype=np.float32))])


class FakeSoundfile:
    @staticmethod
    def write(path, audio, sample_rate):
        with wave.open(path, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(b"\0\0" * len(audio))


class TTSTests(unittest.TestCase):
    def test_legacy_scene_gets_safe_narration_fallback(self):
        text = narration_for_scene({
            "type": "process", "title": "Gradient descent",
            "steps": ["Calculate gradient", "Update weights"],
        })
        self.assertIn("Gradient descent", text)
        self.assertIn("Update weights", text)

    def test_tts_reports_each_scene_and_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            reporter = FakeReporter()
            engine = FakeTTS(cache_dir=Path(directory), reporter=reporter)
            segments = engine.synthesize_plan({
                "scenes": [
                    {"type": "title", "title": "Intro", "narration_text": "Start here"},
                    {"type": "summary", "title": "Summary", "bullets": ["Done"], "narration_text": "Finish here"},
                ]
            })
        self.assertEqual([segment.duration for segment in segments], [4.25, 4.25])
        names = [event[0] for event in reporter.events]
        self.assertIn("tts_started", names)
        self.assertEqual(names.count("tts_scene_completed"), 2)
        self.assertIn("tts_finished", names)

    def test_identical_text_voice_and_speed_reuse_audio_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = FakePipeline()
            engine = KokoroTTS(cache_dir=Path(directory))
            engine._load_dependencies = lambda: (pipeline, FakeSoundfile)
            first = engine.synthesize("Cached narration", 1)
            second = engine.synthesize("Cached narration", 2)
        self.assertEqual(first.path, second.path)
        self.assertEqual(pipeline.calls, 1)


if __name__ == "__main__":
    unittest.main()
