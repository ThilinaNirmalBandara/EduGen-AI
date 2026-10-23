import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.video.renderer import _run_streaming, render_lesson


class RenderingFailureTests(unittest.TestCase):
    def test_failed_preview_prevents_all_movie_rendering_and_preserves_existing_video(self):
        lesson = {'title': 'Process', 'scenes': [
            {'type': 'process', 'title': 'Process', 'steps': ['Measure slope', 'Update weight'], 'duration': 3}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'previous.mp4'
            output.write_bytes(b'previous valid video')
            with patch('src.video.renderer.shutil.which', return_value='/usr/bin/ffmpeg'), \
                 patch('src.video.renderer._run_streaming', side_effect=RuntimeError('VisualQualityError: overlap')) as run:
                with self.assertRaisesRegex(RuntimeError, 'overlap'):
                    render_lesson(lesson, output)
            self.assertEqual(output.read_bytes(), b'previous valid video')
            self.assertEqual(run.call_count, 1)
            self.assertIn('-s', run.call_args.args[0])

    def test_subprocess_error_preserves_actionable_math_failure(self):
        process = Mock()
        process.stdout = io.StringIO('MathRenderingError: correct the equation or install LaTeX\n')
        process.wait.return_value = 1
        with patch('src.video.renderer.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(RuntimeError, 'correct the equation'):
                _run_streaming(['manim'], reporter=Mock())
        self.assertTrue(process.stdout.closed)
