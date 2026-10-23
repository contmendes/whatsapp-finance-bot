import unittest
from unittest.mock import patch

from gemini_handler import GeminiHandler


class GeminiModelSelectionTests(unittest.TestCase):
    def test_defaults_to_gemini_38_flash(self):
        with patch.dict("os.environ", {"GEMINI_MODEL": ""}):
            handler = GeminiHandler()

        self.assertEqual(handler.model, "gemini-3.8-flash")
        self.assertTrue(handler.endpoint.endswith("/models/gemini-3.8-flash:generateContent"))

    def test_migrates_legacy_gemini_25_flash_configuration(self):
        for configured in ("gemini-2.5-flash", "models/gemini-2.5-flash"):
            with self.subTest(configured=configured), patch.dict("os.environ", {"GEMINI_MODEL": configured}):
                handler = GeminiHandler()
                self.assertEqual(handler.model, "gemini-3.8-flash")

    def test_preserves_other_explicit_model_override(self):
        with patch.dict("os.environ", {"GEMINI_MODEL": "gemini-3.6-flash"}):
            handler = GeminiHandler()

        self.assertEqual(handler.model, "gemini-3.6-flash")


if __name__ == "__main__":
    unittest.main()
