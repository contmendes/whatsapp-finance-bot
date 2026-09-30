import base64
import json
import os
import re

import requests


class GeminiHandler:
    """Integração REST com Gemini para áudio e comprovantes.

    A chave é lida exclusivamente de GEMINI_API_KEY no ambiente do Render.
    """

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    @property
    def configured(self):
        return bool(self.api_key)

    def extract_transaction(self, media_bytes, mime_type, context_type="PF", categories=None):
        if not self.configured:
            return None, "GEMINI_API_KEY não configurada no Render."
        if len(media_bytes) > 18 * 1024 * 1024:
            return None, "O arquivo é grande demais para a análise gratuita. Envie um áudio ou foto menor."
        category_text = json.dumps(categories or {}, ensure_ascii=False)
        prompt = f"""Você é a Maria Financeira, assistente financeira brasileira.
Analise o áudio ou comprovante e extraia UM lançamento financeiro. O contexto padrão é {context_type}.
Não invente dados. Se um campo não estiver claro, use null e reduza confidence.
Mantenha a separação PF/PJ: tipo deve ser PF ou PJ.
Categorias disponíveis: {category_text}
Responda SOMENTE JSON válido com exatamente este formato:
{{"tipo":"PF ou PJ","movimento":"DESPESA, RECEITA ou INVESTIMENTO","valor":number|null,"moeda":"BRL","data":"YYYY-MM-DD ou null","estabelecimento":"string ou null","descricao":"string","categoria":"string","confidence":number,"transcricao":"string ou null"}}
"""
        body = {
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(media_bytes).decode("ascii")}},
            ]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "tipo": {"type": "STRING", "enum": ["PF", "PJ"]},
                        "movimento": {"type": "STRING", "enum": ["DESPESA", "RECEITA", "INVESTIMENTO"]},
                        "valor": {"type": "NUMBER", "nullable": True},
                        "moeda": {"type": "STRING"},
                        "data": {"type": "STRING", "nullable": True},
                        "estabelecimento": {"type": "STRING", "nullable": True},
                        "descricao": {"type": "STRING"},
                        "categoria": {"type": "STRING"},
                        "confidence": {"type": "NUMBER"},
                        "transcricao": {"type": "STRING", "nullable": True},
                    },
                    "required": ["tipo", "movimento", "valor", "moeda", "data", "estabelecimento", "descricao", "categoria", "confidence", "transcricao"],
                },
            },
        }
        try:
            response = requests.post(self.endpoint, params={"key": self.api_key}, json=body, timeout=60)
            if response.status_code != 200:
                print(f"[Gemini] erro HTTP {response.status_code}: {response.text[:500]}")
                return None, "Não consegui analisar o arquivo agora."
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
            result = json.loads(text)
            if not isinstance(result.get("valor"), (int, float)) or result["valor"] <= 0:
                return None, "Não consegui identificar um valor com segurança."
            result["tipo"] = result.get("tipo") if result.get("tipo") in ("PF", "PJ") else context_type
            result["confidence"] = float(result.get("confidence") or 0)
            return result, None
        except (KeyError, ValueError, TypeError, requests.RequestException) as error:
            print(f"[Gemini] erro ao interpretar resposta: {error}")
            return None, "Não consegui interpretar o arquivo. Envie uma mensagem de texto para confirmar."
