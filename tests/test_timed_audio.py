import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile

from src.duration import prepare_audio_lesson
from src.schemas import VisualEvent
from src.tts import AudioSegment, KokoroTTS, NarrationTiming
from src.video.timeline import validate_timeline


class TimedAudioTests(unittest.TestCase):
    def test_measured_phrase_boundaries_and_assembled_audio_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = KokoroTTS(cache_dir=Path(directory))
            calls = []
            def synthesize(text, index):
                calls.append(text)
                path = Path(directory) / (hashlib.sha256(text.encode()).hexdigest() + '.wav')
                # Different sample lengths prove times come from audio, not word estimates.
                duration = 1.25 if len(calls) == 1 else 2.5
                soundfile.write(path, np.zeros(round(duration*100)), 100)
                return AudioSegment(path, duration)
            engine.synthesize = synthesize
            text = 'Calculate the gradient. Apply the update.'
            first = engine.synthesize_timed(text, 1)
            second = engine.synthesize_timed(text, 1)
            self.assertEqual(len(calls), 2)
            self.assertEqual(first, second)
            self.assertEqual(first.timings[1].start, 1.25)
            self.assertEqual(first.duration, 3.75)
            self.assertAlmostEqual(soundfile.info(first.path).duration, first.duration)

    def test_cues_must_match_the_spoken_narration(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = KokoroTTS(cache_dir=Path(directory))
            with self.assertRaisesRegex(RuntimeError, 'disagree'):
                engine.synthesize_timed('Speak this.', 1, cues=[{'narration_text': 'Something different.'}])

    def test_overlong_audio_is_split_without_resynthesizing_or_losing_cues(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'narration.wav'
            soundfile.write(path, np.zeros(6000), 100)
            timings = tuple(NarrationTiming(f'Phrase {i}.', i*7.5, 7.5, i) for i in range(8))
            cues = [VisualEvent(narration_text=t.text, action='update', parameters={'iteration': i}).model_dump()
                    for i, t in enumerate(timings)]
            scene = {'type': 'graph', 'title': 'Descent', 'duration': 30,
                     'narration_text': ' '.join(t.text for t in timings), 'visual_timeline': cues}
            class NoSynthesis:
                def synthesize(self, *args):
                    raise AssertionError('Splitting measured audio must reuse the waveform')
            prepared = prepare_audio_lesson({'title': 'Demo', 'scenes': [scene]},
                                           [AudioSegment(path, 60, timings)], NoSynthesis())
            self.assertEqual(len(prepared.lesson['scenes']), 2)
            self.assertAlmostEqual(prepared.total_audio_duration, 60)
            self.assertTrue(all(a.duration <= 44.5 for a in prepared.audio_segments))
            for subscene in prepared.lesson['scenes']:
                validate_timeline(subscene)
            self.assertEqual(prepared.lesson['scenes'][1]['visual_timeline'][0]['parameters']['iteration'], 5)
            self.assertEqual(' '.join(s['narration_text'] for s in prepared.lesson['scenes']), scene['narration_text'])
