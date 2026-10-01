import os
import re

import requests

from categories import CATEGORIES

WHATSAPP_API_URL = os.getenv("WHATSAPP_API_URL", "https://graph.facebook.com/v23.0")


class WhatsAppHandler:
    def __init__(self, phone_number_id, access_token):
        self.phone_number_id = phone_number_id
        self.access_token = access_token

    def send_message(self, to_number, message_text):
        if not self.phone_number_id or not self.access_token:
            print("[WhatsApp] PHONE_NUMBER_ID ou ACCESS_TOKEN não configurado")
            return False
        url = f"{WHATSAPP_API_URL}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "text",
            "text": {"preview_url": False, "body": message_text},
        }
        try:
            response = requests.post(url, headers=headers, json=data, timeout=20)
            if response.status_code != 200:
                print(f"[WhatsApp] erro HTTP {response.status_code}: {response.text}")
                return False
            return True
        except requests.RequestException as error:
            print(f"[WhatsApp] erro de rede: {error}")
            return False

    @staticmethod
    def _extract_amount(text):
        match = re.search(r"(?<!\w)(?:r\$\s*)?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|(?:r\$\s*)?\d+(?:[\.,]\d{1,2})?(?!\w)", text, flags=re.I)
        if not match:
            return None, text
        raw = match.group(0).lower().replace("r$", "").replace(" ", "")
        try:
            value = float(raw.replace(".", "").replace(",", ".")) if "," in raw or re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw) else float(raw)
        except ValueError:
            return None, text
        return value, (text[:match.start()] + " " + text[match.end():]).strip()

    def parse_message(self, message_text, categories=None, default_type="PF"):
        try:
            original = message_text.strip()
            normalized = original.upper()
            tipo_pj_pf = "PJ" if re.search(r"\bPJ\b|EMPRESA|CNPJ|NEGÓCIO", normalized) else "PF" if re.search(r"\bPF\b|PESSOAL|CASA", normalized) else default_type
            if re.search(r"\b(RECEITA|RECEBI|ENTROU|GANHEI|SALÁRIO|SALARIO|VENDA)\b", normalized):
                tipo_movimento = "RECEITA"
            elif re.search(r"\b(INVESTIMENTO|INVESTI|APLIQUEI)\b", normalized):
                tipo_movimento = "INVESTIMENTO"
            else:
                tipo_movimento = "DESPESA"
            valor, without_value = self._extract_amount(original)
            if valor is None:
                return None, None, None, None, None, "❌ Não encontrei um valor. Exemplo: *gastei 45,90 no almoço*"
            if valor <= 0:
                return None, None, None, None, None, "❌ O valor deve ser maior que zero"
            descricao = re.sub(r"r\$|\b(pf|pj|pessoal|empresa|receita|despesa|investimento|recebi|entrou|gastei|paguei|comprei|ganhei|investi|de|no|na|em)\b", " ", without_value, flags=re.I)
            descricao = re.sub(r"\s+", " ", descricao).strip(" -:,.\"") or "Sem descrição"
            source = (categories or CATEGORIES).get("GERAL", {}).get("INVESTIMENTO", []) if tipo_movimento == "INVESTIMENTO" else (categories or CATEGORIES).get(tipo_pj_pf, {}).get(tipo_movimento, [])
            categoria = next((cat for cat in source if cat.casefold() in descricao.casefold()), "Outro")
            return tipo_pj_pf, tipo_movimento, categoria, valor, descricao, None
        except Exception as error:
            return None, None, None, None, None, f"❌ Erro ao processar: {error}"

    def get_help_message(self):
        return """📱 *Maria Financeira*
	Você pode falar comigo de forma natural, sem formato fixo:
• *gastei 45,90 no almoço*
• *recebi 2.000 de salário PF*
• *paguei 350 de software na empresa*
• *investi 500 em ações*

Se não indicar PF/PJ, uso PF. Comandos:
• *Resumo PF* ou *Resumo PJ*
• *Categorias*
• *Categoria adicionar PF despesa Mercado*
• *Categoria remover PF despesa Mercado*
• *Ajuda*

	Também posso responder perguntas como:
	• *quanto gastei com alimentação este mês?*
	• *onde gastei mais?*
	• *mostre meu resumo PJ*
	• *troque meu contexto para PF*

	Áudios, fotos e PDFs de comprovantes são analisados pelo Gemini e sempre pedem sua confirmação antes de registrar."""
