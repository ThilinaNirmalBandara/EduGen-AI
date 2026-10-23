import logging
import io
import tempfile
import unittest
from pathlib import Path

from rich.console import Console

from src.progress import ExecutionReporter, configure_logging


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.console = Console(record=True, width=100)
        self.reporter = ExecutionReporter(console=self.console, logger=logging.getLogger("test-edugen"))

    def test_reports_scene_completion_and_output(self):
        scenes = [{"title": "Introduction"}, {"title": "Summary"}]
        self.reporter.rendering_started(scenes)
        self.reporter.scene_started(1, 2, "Introduction")
        self.reporter.scene_completed(1, 2, "Introduction")
        self.assertEqual(self.reporter._scene_progress.tasks[0].completed, 1)
        self.reporter.rendering_finished(__import__("pathlib").Path("outputs/test.mp4"))
        output = self.console.export_text()
        self.assertIn("Rendering complete", output)
        self.assertIn("outputs/test.mp4", output)

    def test_failure_stops_active_render_progress(self):
        self.reporter.rendering_started([{"title": "Introduction"}])
        self.reporter.failed(RuntimeError("renderer failed"))
        self.assertIsNone(self.reporter._scene_progress)
        self.assertIn("Generation failed", self.console.export_text())

    def test_failure_logs_traceback_without_echoing_through_root_logger(self):
        with tempfile.TemporaryDirectory() as directory:
            root_output = io.StringIO()
            root_handler = logging.StreamHandler(root_output)
            root = logging.getLogger()
            root.addHandler(root_handler)
            logger, log_path = configure_logging(Path(directory))
            console = Console(file=io.StringIO(), record=True)
            reporter = ExecutionReporter(console=console, logger=logger)
            try:
                try:
                    raise RuntimeError("expansion failed")
                except RuntimeError as error:
                    reporter.failed(error)
                for handler in logger.handlers:
                    handler.flush()
                self.assertIn("Traceback", log_path.read_text())
                self.assertEqual(root_output.getvalue(), "")
                self.assertNotIn("Traceback", console.export_text())
            finally:
                root.removeHandler(root_handler)
                for handler in logger.handlers:
                    handler.close()
                logger.handlers.clear()

    def test_final_validation_failure_does_not_announce_another_retry(self):
        self.reporter.validation_retry(3, "too little narration")
        output = self.console.export_text()
        self.assertIn("all 3 attempts exhausted", output)
        self.assertNotIn("retry 3 requested", output)

    def test_cancelling_narration_stops_progress(self):
        self.reporter.tts_started(2, 'af_heart', 1)
        self.reporter.cancelled()
        self.assertIsNone(self.reporter._tts_progress)


if __name__ == "__main__":
    unittest.main()
