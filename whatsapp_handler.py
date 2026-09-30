import os

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

    def parse_message(self, message_text):
        try:
            message_text = message_text.strip()
            if ":" not in message_text:
                return None, None, None, None, None, "❌ Formato inválido. Use: 'Despesa PF: 150 Alimentação'"

            parte1, resto = message_text.split(":", 1)
            header = parte1.strip().upper()
            resto = resto.strip()

            tipo_movimento = next((tipo for tipo in ["RECEITA", "DESPESA", "INVESTIMENTO"] if tipo in header), None)
            if not tipo_movimento:
                return None, None, None, None, None, "❌ Use 'Receita', 'Despesa' ou 'Investimento'"

            tipo_pj_pf = "PJ" if "PJ" in header else "PF" if "PF" in header else None
            if not tipo_pj_pf:
                return None, None, None, None, None, "❌ Especifique PJ ou PF"

            partes = resto.split(None, 1)
            try:
                valor = float(partes[0].replace(".", "").replace(",", ".")) if "," in partes[0] else float(partes[0])
            except (ValueError, IndexError):
                return None, None, None, None, None, "❌ Valor inválido. Use números, por exemplo: 1000 ou 1000,50"
            if valor <= 0:
                return None, None, None, None, None, "❌ O valor deve ser maior que zero"

            descricao = partes[1] if len(partes) > 1 else "Sem descrição"
            categorias_validas = CATEGORIES["GERAL"]["INVESTIMENTO"] if tipo_movimento == "INVESTIMENTO" else CATEGORIES[tipo_pj_pf][tipo_movimento]
            categoria = next((cat for cat in categorias_validas if cat.lower() in descricao.lower()), "Outro")
            return tipo_pj_pf, tipo_movimento, categoria, valor, descricao, None
        except Exception as error:
            return None, None, None, None, None, f"❌ Erro ao processar: {error}"

    def get_help_message(self):
        return """📱 *Maria Financeira*

*Como usar:*
"Receita PJ: 2000 Venda de Serviço"
"Despesa PF: 150 Alimentação"
"Investimento PF: 500 Ações"

*Comandos:*
• "Resumo PJ" ou "Resumo PF"
• "Categorias"
• "Ajuda"""
