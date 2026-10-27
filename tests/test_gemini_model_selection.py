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

    def test_interpret_text_returns_category_specific_confidence(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"candidates": [{"content": {"parts": [{
                    "text": '{"intent":"launch","confidence":0.9,"categoria_confidence":0.82,"tipo":"PF","contexto":"PF"}'
                }]}}]}

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": ""}), \
             patch("gemini_handler.requests.post", return_value=FakeResponse()) as post:
            handler = GeminiHandler()
            result, error = handler.interpret_text("almocei por R$ 30", "PF", {}, {"categories": ["Alimentação"]})

        self.assertIsNone(error)
        self.assertEqual(result["categoria_confidence"], 0.82)
        schema = post.call_args.kwargs["json"]["generationConfig"]["responseSchema"]
        self.assertIn("categoria_confidence", schema["properties"])
        self.assertIn("categoria_confidence", schema["required"])

    def test_interpret_business_text_restricts_plan_account_to_allowed_options(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"candidates": [{"content": {"parts": [{
                    "text": '{"movimento":"DESPESA","valor":30,"data":null,"plano_conta":"Contratos","descricao_tipo":"contratos Isabella","confidence":0.9}'
                }]}}]}

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": ""}), \
             patch("gemini_handler.requests.post", return_value=FakeResponse()) as post:
            handler = GeminiHandler()
            result, error = handler.interpret_business_text(
                "Paguei R$ 30 para contratos Isabella", ["Contratos", "Material"]
            )

        self.assertIsNone(error)
        self.assertEqual(result["plano_conta"], "Contratos")
        self.assertEqual(result["descricao_tipo"], "contratos Isabella")
        prompt = post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn("Paguei R$ 30 para contratos Isabella", prompt)

        class BadOptionResponse(FakeResponse):
            @staticmethod
            def json():
                data = FakeResponse.json()
                data["candidates"][0]["content"]["parts"][0]["text"] = data["candidates"][0]["content"]["parts"][0]["text"].replace("Contratos", "Item inexistente")
                return data

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key", "GEMINI_MODEL": ""}), \
             patch("gemini_handler.requests.post", return_value=BadOptionResponse()):
            result, error = GeminiHandler().interpret_business_text("texto", ["Contratos"])
        self.assertIsNone(error)
        self.assertIsNone(result["plano_conta"])


if __name__ == "__main__":
    unittest.main()
