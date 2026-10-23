import unittest
from unittest.mock import patch

from src.dependencies import DependencyCheckError, check_startup


class DependencyTests(unittest.TestCase):
    def test_missing_ffprobe_fails_before_ollama(self):
        with patch('src.dependencies.shutil.which',side_effect=lambda name:'/usr/bin/ffmpeg' if name=='ffmpeg' else None),patch('src.dependencies.importlib.import_module'),patch('src.llm.OllamaClient.generate_json') as generate:
            with self.assertRaisesRegex(DependencyCheckError,'FFprobe'):
                check_startup(require_ollama=True)
            generate.assert_not_called()

    def test_missing_ffmpeg_has_installation_guidance(self):
        with patch("src.dependencies.shutil.which", return_value=None), \
             patch("src.dependencies.importlib.import_module"):
            with self.assertRaises(DependencyCheckError) as context:
                check_startup(require_ollama=False)
        self.assertIn("FFmpeg", str(context.exception))
        self.assertIn("apt install", str(context.exception))


if __name__ == "__main__":
    unittest.main()
