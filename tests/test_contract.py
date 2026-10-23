import unittest

from src.video.contract import validate_lesson


class ContractTests(unittest.TestCase):
    def test_valid_lesson(self):
        plan = {"title": "Demo", "scenes": [{"type": "title", "title": "Demo", "duration": 3}]}
        self.assertEqual(validate_lesson(plan), plan)

    def test_bad_type(self):
        plan = {"title": "Demo", "scenes": [{"type": "python_exec", "title": "Bad", "duration": 3}]}
        with self.assertRaises(ValueError):
            validate_lesson(plan)

    def test_bad_duration(self):
        plan = {"title": "Demo", "scenes": [{"type": "title", "title": "Demo", "duration": 1}]}
        with self.assertRaises(ValueError):
            validate_lesson(plan)


if __name__ == "__main__":
    unittest.main()
