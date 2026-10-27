import re
import unicodedata
from datetime import datetime


def _norm(value):
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _match(value, options):
    key = _norm(value)
    if not key:
        return None
    return next((item for item in options if _norm(item) == key), None)


def _numbered(value, options):
    match = re.fullmatch(r"(?:opcao\s*)?(\d+)", _norm(value))
    if not match:
        return None
    index = int(match.group(1)) - 1
    return options[index] if 0 <= index < len(options) else None


def _menu(title, options):
    if not options:
        return f"Não encontrei opções válidas para {title}. Peça ao responsável para revisar a configuração da planilha."
    return title + "\n" + "\n".join(f"{i}. {name}" for i, name in enumerate(options, start=1))


class BusinessFlow:
    """Conversational writer for the separate business cost-center spreadsheet."""

    def __init__(self, sheets, gemini, whatsapp, centers):
        self.sheets = sheets
        self.gemini = gemini
        self.whatsapp = whatsapp
        self.centers = list(dict.fromkeys(str(item).strip() for item in centers if str(item).strip()))
        self.pending = {}

    def _initial_values(self, message):
        value, without_value = self.whatsapp._extract_amount(message)
        date_value = self.whatsapp.extract_date(message)
        upper = _norm(message)
        movement = None
        if re.search(r"\b(recebi|receita|entrada|venda|faturei|caiu)\b", upper):
            movement = "RECEITA"
        elif re.search(r"\b(investi|investimento|apliquei)\b", upper):
            movement = "INVESTIMENTO"
        elif re.search(r"\b(paguei|gastei|comprei|despesa|saida|saída|custo|custa|pagar)\b", upper):
            movement = "DESPESA"
        # Fallback preserves the user's purpose text and removes amount/transaction verbs.
        type_description = re.sub(r"r\$", " ", without_value, flags=re.I)
        type_description = re.sub(
            r"\b(reais?|paguei|pagamento|pagar|gastei|gasto|comprei|compra|recebi|receita|entrada|venda|vendi|faturei|investi|investimento|apliquei|para|por|de|no|na|do|da|em|um|uma)\b",
            " ", type_description, flags=re.I,
        )
        type_description = " ".join(type_description.split()).strip(" -:,.\"'")
        return {"value": value, "date": date_value, "movement": movement, "type_description": type_description}

    def _send_missing(self, phone, transaction):
        if not transaction.get("center"):
            transaction["awaiting"] = "center"
            self.pending[phone] = transaction
            return _menu("Qual é o centro de custo? Responda pelo número ou nome da aba:", self.centers)
        if not transaction.get("plan_options"):
            transaction["plan_options"] = self.sheets.get_business_plan_options(transaction["center"])
        if not transaction.get("plan_account"):
            if self.gemini.configured:
                extracted, _ = self.gemini.interpret_business_text(
                    transaction["raw_message"], transaction["plan_options"]
                )
                if extracted:
                    candidate = extracted.get("plano_conta")
                    if candidate in transaction["plan_options"] and float(extracted.get("confidence") or 0) >= 0.65:
                        transaction["plan_account"] = candidate
                    transaction["type_description"] = transaction.get("type_description") or extracted.get("descricao_tipo") or ""
                    transaction["movement"] = transaction.get("movement") or extracted.get("movimento")
                    transaction["value"] = transaction.get("value") or extracted.get("valor")
                    transaction["date"] = transaction.get("date") or extracted.get("data")
            if not transaction.get("plan_account"):
                transaction["awaiting"] = "plan_account"
                self.pending[phone] = transaction
                return _menu("Qual item do Plano de Contas devo usar? Responda pelo número ou nome:", transaction["plan_options"])
        if not transaction.get("type_description"):
            transaction["awaiting"] = "type_description"
            self.pending[phone] = transaction
            return "Qual é o tipo/motivo deste lançamento? Ex.: *contratos Isabella*."
        if not transaction.get("movement"):
            transaction["awaiting"] = "movement"
            self.pending[phone] = transaction
            return "É uma *despesa/saída* ou *receita/entrada*? Responda por exemplo: *despesa* ou *receita*."
        if not transaction.get("value"):
            transaction["awaiting"] = "value"
            self.pending[phone] = transaction
            return "Qual foi o valor? Envie somente o valor, por exemplo: *R$ 30,00*."
        response = self.sheets.add_business_transaction(
            center_sheet=transaction["center"], movement=transaction["movement"],
            plan_account=transaction["plan_account"], type_description=transaction["type_description"],
            description=transaction["raw_message"], value=transaction["value"],
            transaction_date=transaction.get("date"), message_id=transaction.get("message_id"),
            plan_options=transaction["plan_options"], allowed_centers=self.centers,
        )
        if response[0]:
            self.pending.pop(phone, None)
            return response[1]
        transaction["awaiting"] = "retry"
        self.pending[phone] = transaction
        return response[1] + "\n\nO lançamento continua pendente. Responda *TENTAR NOVAMENTE* ou *CANCELAR*."

    def process(self, phone, message, message_id=""):
        text = str(message or "").strip()
        key = _norm(text)
        if key in ("ajuda empresa", "ajuda empresarial"):
            return ("📊 *Registro empresarial*\nEnvie algo como: *paguei R$ 30 para contratos Isabella*. "
                    "Vou pedir o centro de custo e o item do plano de contas se não estiverem claros. "
                    "A mensagem completa ficará na coluna Descrição.")
        transaction = self.pending.get(phone)
        if transaction:
            if key in ("cancelar", "cancela"):
                self.pending.pop(phone, None)
                return "Tudo bem, não registrei esse lançamento."
            if transaction.get("awaiting") == "retry":
                if key not in ("tentar novamente", "repetir", "sim"):
                    return "Responda *TENTAR NOVAMENTE* para salvar ou *CANCELAR* para descartar."
                transaction.pop("awaiting", None)
                return self._send_missing(phone, transaction)
            field = transaction.get("awaiting")
            if field == "center":
                choice = _numbered(text, self.centers) or _match(text, self.centers)
                if not choice:
                    return _menu("Escolha um centro de custo pelo número ou nome:", self.centers)
                transaction["center"] = choice
            elif field == "plan_account":
                options = transaction.get("plan_options", [])
                choice = _numbered(text, options) or _match(text, options)
                if not choice:
                    return _menu("Escolha uma opção válida do Plano de Contas:", options)
                transaction["plan_account"] = choice
            elif field == "type_description":
                transaction["type_description"] = text
            elif field == "movement":
                normalized = _norm(text)
                if any(token in normalized.split() for token in ("receita", "entrada", "recebi")):
                    transaction["movement"] = "RECEITA"
                elif any(token in normalized.split() for token in ("despesa", "saida", "gasto", "paguei")):
                    transaction["movement"] = "DESPESA"
                elif any(token in normalized.split() for token in ("investimento", "investi")):
                    transaction["movement"] = "INVESTIMENTO"
                else:
                    return "Responda *despesa*, *receita* ou *investimento*."
            elif field == "value":
                value, _ = self.whatsapp._extract_amount(text)
                if not value or value <= 0:
                    return "Não identifiquei um valor válido. Envie, por exemplo, *R$ 30,00*."
                transaction["value"] = value
            transaction.pop("awaiting", None)
            return self._send_missing(phone, transaction)

        values = self._initial_values(text)
        if not values["value"]:
            return "Não encontrei o valor. Envie a movimentação com o valor, por exemplo: *paguei R$ 30 para contratos Isabella*."
        transaction = {
            **values, "raw_message": text, "message_id": message_id, "center": "",
            "plan_account": "", "plan_options": [],
        }
        explicit_center = next((center for center in self.centers if _norm(center) in key), None)
        if explicit_center:
            transaction["center"] = explicit_center
        if not self.centers:
            return "O registro empresarial ainda não está habilitado: faltam configurar os nomes exatos das abas de centro de custo."
        if len(self.centers) == 1 and not transaction["center"]:
            transaction["center"] = self.centers[0]
        self.pending[phone] = transaction
        return self._send_missing(phone, transaction)

    def process_media(self, phone, extracted, raw_message, message_id=""):
        text = str(raw_message or "").strip()
        if not extracted or not extracted.get("valor"):
            return "Não consegui identificar o valor com segurança. Envie o lançamento por texto, por exemplo: *paguei R$ 30 para contratos Isabella*."
        transaction = {
            "value": extracted.get("valor"),
            "date": extracted.get("data"),
            "movement": extracted.get("movimento"),
            "type_description": extracted.get("descricao_tipo") or extracted.get("descricao") or "",
            "raw_message": text or extracted.get("descricao") or "Mídia recebida pelo WhatsApp",
            "message_id": message_id,
            "center": next((center for center in self.centers if _norm(center) in _norm(text)), ""),
            "plan_account": "",
            "plan_options": [],
        }
        if not self.centers:
            return "O registro empresarial ainda não está habilitado: faltam configurar os nomes exatos das abas de centro de custo."
        if len(self.centers) == 1 and not transaction["center"]:
            transaction["center"] = self.centers[0]
        self.pending[phone] = transaction
        return self._send_missing(phone, transaction)
