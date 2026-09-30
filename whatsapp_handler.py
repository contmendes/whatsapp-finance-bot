import requests
import os
from categories import CATEGORIES

WHATSAPP_API_URL = "https://graph.instagram.com/v18.0"
PHONE_NUMBER_ID = os.getenv('PHONE_NUMBER_ID')
ACCESS_TOKEN = os.getenv('ACCESS_TOKEN')

class WhatsAppHandler:
    def __init__(self, phone_number_id, access_token):
        self.phone_number_id = phone_number_id
        self.access_token = access_token

    def send_message(self, to_number, message_text):
        """Envia uma mensagem de texto via WhatsApp"""
        try:
            url = f"{WHATSAPP_API_URL}/{self.phone_number_id}/messages"
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json"
            }

            data = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_number,
                "type": "text",
                "text": {
                    "preview_url": False,
                    "body": message_text
                }
            }

            response = requests.post(url, headers=headers, json=data)
            return response.status_code == 200

        except Exception as e:
            print(f"❌ Erro ao enviar mensagem: {e}")
            return False

    def parse_message(self, message_text):
        """
        Interpreta a mensagem do usuário

        Formato esperado:
        "Receita PJ: 1000 Venda de Serviço" ou
        "Despesa PF: 150 Alimentação"

        Retorna: (tipo_pj_pf, tipo_movimento, categoria, valor, descricao, erro)
        """
        try:
            message_text = message_text.strip()

            # Dividir por ':'
            if ':' not in message_text:
                return None, None, None, None, None, "❌ Formato inválido. Use: 'Receita PJ: 1000 Descrição'"

            parte1, resto = message_text.split(':', 1)
            parte1 = parte1.strip()
            resto = resto.strip()

            # Identificar tipo de movimento (RECEITA, DESPESA, INVESTIMENTO)
            tipo_movimento = None
            for tipo in ["RECEITA", "DESPESA", "INVESTIMENTO"]:
                if tipo in parte1.upper():
                    tipo_movimento = tipo
                    break

            if not tipo_movimento:
                return None, None, None, None, None, "❌ Use 'Receita', 'Despesa' ou 'Investimento'"

            # Identificar PJ ou PF
            tipo_pj_pf = None
            if "PJ" in parte1.upper():
                tipo_pj_pf = "PJ"
            elif "PF" in parte1.upper():
                tipo_pj_pf = "PF"
            else:
                return None, None, None, None, None, "❌ Especifique PJ ou PF"

            # Extrair valor e categoria/descrição
            partes = resto.split(' ', 1)
            try:
                valor = float(partes[0].replace(',', '.'))
            except:
                return None, None, None, None, None, "❌ Valor inválido. Use números (ex: 1000 ou 1000.50)"

            categoria_descricao = partes[1] if len(partes) > 1 else "Sem descrição"

            # Validar categoria
            if tipo_movimento == "INVESTIMENTO":
                categorias_validas = CATEGORIES["GERAL"]["INVESTIMENTO"]
            else:
                categorias_validas = CATEGORIES[tipo_pj_pf][tipo_movimento]

            categoria_encontrada = None
            for cat in categorias_validas:
                if cat.lower() in categoria_descricao.lower():
                    categoria_encontrada = cat
                    break

            if not categoria_encontrada:
                categoria_encontrada = "Outro"

            return tipo_pj_pf, tipo_movimento, categoria_encontrada, valor, categoria_descricao, None

        except Exception as e:
            return None, None, None, None, None, f"❌ Erro ao processar: {str(e)}"

    def get_help_message(self):
        """Retorna mensagem de ajuda"""
        return """
📱 *Assistente Financeiro*

*Como usar:*
"Receita PJ: 2000 Venda de Serviço"
"Despesa PF: 150 Alimentação"
"Investimento: 500 Ações"

*Comandos:*
• "Resumo" - Mostra resumo do mês
• "Categorias" - Lista categorias
• "Ajuda" - Esta mensagem

*Exemplos:*
• "Receita PJ: 5000 Freelancer"
• "Despesa PJ: 200 Software"
• "Despesa PF: 50 Uber"
• "Investimento: 1000 Poupança"
"""
