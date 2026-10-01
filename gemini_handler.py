import base64
import json
import os
import re

import requests


class GeminiHandler:
    """Integração REST com Gemini para texto, áudio, comprovantes e PDFs."""

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
Analise o áudio, imagem ou PDF e extraia UM lançamento financeiro. O contexto padrão é {context_type}.
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
            print(f"[Gemini] erro ao interpretar arquivo: {error}")
            return None, "Não consegui interpretar o arquivo. Envie uma mensagem de texto para confirmar."

    def interpret_text(self, message, context_type="PF", categories=None):
        """Interpreta texto livre em uma intenção segura para o fluxo da Maria."""
        if not self.configured:
            return None, "Gemini não configurado"
        category_text = json.dumps(categories or {}, ensure_ascii=False)
        prompt = f"""Você é a Maria Financeira, assistente financeira brasileira no WhatsApp.
Interprete a mensagem do usuário em linguagem natural. O contexto PF/PJ atual é {context_type}.
Não execute ações; apenas classifique a intenção e extraia os dados.
A separação PF/PJ é obrigatória. Se o usuário não mencionar, use {context_type}.
Categorias disponíveis: {category_text}
Mensagem: {message}
Responda SOMENTE JSON com este formato:
{{"intent":"launch|summary|balance|search|profile|categories|switch_context|help|delete_last|edit_last|unknown","tipo":"PF ou PJ","movimento":"DESPESA|RECEITA|INVESTIMENTO","valor":number,"categoria":"string","descricao":"string","keyword":"string","mes":"YYYY-MM ou todos","contexto":"PF ou PJ","confidence":number,"reply":"string"}}
"""
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "intent": {"type": "STRING", "enum": ["launch", "summary", "balance", "search", "profile", "categories", "switch_context", "help", "delete_last", "edit_last", "unknown"]},
                        "tipo": {"type": "STRING", "enum": ["PF", "PJ"]},
                        "movimento": {"type": "STRING", "enum": ["DESPESA", "RECEITA", "INVESTIMENTO"]},
                        "valor": {"type": "NUMBER", "nullable": True},
                        "categoria": {"type": "STRING"},
                        "descricao": {"type": "STRING"},
                        "keyword": {"type": "STRING"},
                        "mes": {"type": "STRING"},
                        "contexto": {"type": "STRING", "enum": ["PF", "PJ"]},
                        "confidence": {"type": "NUMBER"},
                        "reply": {"type": "STRING"},
                    },
                    "required": ["intent", "tipo", "movimento", "valor", "categoria", "descricao", "keyword", "mes", "contexto", "confidence", "reply"],
                },
            },
        }
        try:
            response = requests.post(self.endpoint, params={"key": self.api_key}, json=body, timeout=30)
            if response.status_code != 200:
                print(f"[Gemini] erro HTTP na interpretação: {response.status_code}: {response.text[:300]}")
                return None, "Falha temporária"
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
            result = json.loads(text)
            result["confidence"] = float(result.get("confidence") or 0)
            if result.get("tipo") not in ("PF", "PJ"):
                result["tipo"] = context_type
            if result.get("contexto") not in ("PF", "PJ"):
                result["contexto"] = context_type
            return result, None
        except (KeyError, ValueError, TypeError, requests.RequestException) as error:
            print(f"[Gemini] erro ao interpretar texto: {error}")
            return None, "Falha temporária"
