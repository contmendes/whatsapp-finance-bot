import os
import re
import unicodedata
from datetime import datetime

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
        # Prefere agrupamentos completos (2.000,00) e não deixa a primeira
        # alternativa truncar inteiros de quatro ou mais dígitos (2000 -> 200).
        pattern = re.compile(r"(?<!\w)(?:r\$\s*)?((?:\d{1,3}(?:\.\d{3})+|\d+)(?:[.,]\d{1,2})?)(?!\w)", flags=re.I)
        matches = list(pattern.finditer(text))
        if not matches:
            return None, text
        date_spans = [m.span() for m in re.finditer(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", text)]
        date_spans += [m.span() for m in re.finditer(
            r"\b(?:dia\s+)?\d{1,2}\s+de\s+(?:janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)(?:\s+de\s+\d{4})?\b",
            text, flags=re.I)]
        not_date = [m for m in matches if not any(m.start() < end and m.end() > start for start, end in date_spans)]
        currency_matches = [m for m in matches if re.match(r"r\$\s*", text[m.start():m.end()], flags=re.I)]
        match = (currency_matches or not_date or matches)[0]
        raw = match.group(1).replace(" ", "")
        try:
            if "," in raw:
                value = float(raw.replace(".", "").replace(",", "."))
            elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
                value = float(raw.replace(".", ""))
            else:
                value = float(raw)
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
            def normalized(value):
                value = unicodedata.normalize("NFD", str(value or "").casefold())
                value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
                return re.sub(r"[^a-z0-9]+", " ", value).strip()
            normalized_message = normalized(original)
            categoria = next((cat for cat in sorted(source, key=len, reverse=True)
                              if normalized(cat) and f" {normalized(cat)} " in f" {normalized_message} "), "Outro")
            return tipo_pj_pf, tipo_movimento, categoria, valor, descricao, None
        except Exception as error:
            return None, None, None, None, None, f"❌ Erro ao processar: {error}"

    @staticmethod
    def extract_date(text):
        """Extrai datas brasileiras e referências a mês em português."""
        original = text or ""
        normalized = original.casefold()
        today = datetime.now()
        months = {
            "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3,
            "abril": 4, "maio": 5, "junho": 6, "julho": 7,
            "agosto": 8, "setembro": 9, "outubro": 10,
            "novembro": 11, "dezembro": 12,
        }
        match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", normalized)
        if match:
            day, month = int(match.group(1)), int(match.group(2))
            year = int(match.group(3)) if match.group(3) else today.year
            if year < 100:
                year += 2000
            try:
                return datetime(year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                return None
        match = re.search(r"(?:dia\s+)?(\d{1,2})\s+de\s+([a-zç]+)(?:\s+de\s+(\d{4}))?", normalized)
        if match and match.group(2) in months:
            year = int(match.group(3)) if match.group(3) else today.year
            try:
                return datetime(year, months[match.group(2)], int(match.group(1))).strftime("%Y-%m-%d")
            except ValueError:
                return None
        for name, month in months.items():
            if re.search(rf"\b(?:em|no mês de|mes de)\s+{re.escape(name)}\b", normalized):
                return datetime(today.year, month, 1).strftime("%Y-%m-%d")
        if re.search(r"\b(hoje|agora)\b", normalized):
            return today.strftime("%Y-%m-%d")
        if re.search(r"\bontem\b", normalized):
            from datetime import timedelta
            return (today - timedelta(days=1)).strftime("%Y-%m-%d")
        return None

    @staticmethod
    def extract_payment_details(text):
        """Extrai banco e meio de pagamento sem exigir formato rígido."""
        normalized = (text or "").casefold()
        bank = ""
        payment = ""
        for name in ("nubank", "inter", "itau", "itaú", "bradesco", "santander", "caixa", "bb", "banco do brasil", "mercado pago", "picpay"):
            if re.search(rf"\b{re.escape(name)}\b", normalized):
                bank = name.title()
                break
        payment_patterns = [
            (r"pix", "Pix"), (r"cartão de crédito|cartao de credito|crédito|credito", "Cartão de crédito"),
            (r"cartão de débito|cartao de debito|débito|debito", "Cartão de débito"),
            (r"dinheiro|espécie|especie", "Dinheiro"), (r"boleto", "Boleto"),
            (r"transferência|transferencia|ted|doc", "Transferência"),
        ]
        for pattern, label in payment_patterns:
            if re.search(pattern, normalized):
                payment = label
                break
        return bank, payment

    def get_help_message(self):
        return """📱 *Maria Financeira*
Você pode falar comigo de forma natural, sem formato fixo:
• *gastei 45,90 no almoço*
• *recebi 2.000 de salário PF*
• *paguei 350 de software na empresa*
• *investi 500 em ações*
• Também posso ler fotos de comprovantes e transcrever áudios.

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

Se Categoria, Meta, Conta ou Status não estiver claro, faço uma pergunta e uso apenas opções válidas da planilha. Para áudio, a transcrição completa fica na descrição; em foto, uso a legenda ou o texto/descrição lido do comprovante.
Áudios, fotos e PDFs sempre pedem sua confirmação antes de registrar."""
