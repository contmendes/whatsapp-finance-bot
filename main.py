import hashlib
import hmac
import os
import re
import unicodedata
from threading import Lock, Thread

import requests

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from categories import get_categories_text
from gemini_handler import GeminiHandler
from sheets_handler import SheetsHandler
from whatsapp_handler import WhatsAppHandler

load_dotenv()

app = Flask(__name__)

whatsapp = WhatsAppHandler(
    phone_number_id=os.getenv("PHONE_NUMBER_ID"),
    access_token=os.getenv("ACCESS_TOKEN"),
)
sheets = SheetsHandler()
gemini = GeminiHandler()
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
APP_SECRET = os.getenv("APP_SECRET", "")
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "").strip()
LAYOUT_SYNC_TOKEN = os.getenv("LAYOUT_SYNC_TOKEN", "").strip()
pending_deletions = {}
pending_transactions = {}
received_message_ids = set()
received_message_lock = Lock()
layout_sync_status = {"state": "idle"}


def is_valid_signature() -> bool:
    if not APP_SECRET:
        print("[Webhook] APP_SECRET não configurado")
        return False
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"), request.get_data(cache=True), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def download_whatsapp_media(media_id):
    """Obtém bytes do arquivo pela Cloud API sem persistir mídia no Render."""
    headers = {"Authorization": f"Bearer {whatsapp.access_token}"}
    meta = requests.get(f"https://graph.facebook.com/v23.0/{media_id}", headers=headers, timeout=20)
    meta.raise_for_status()
    media_url = meta.json().get("url")
    if not media_url:
        raise ValueError("WhatsApp não retornou URL de mídia")
    file_response = requests.get(media_url, headers=headers, timeout=30)
    file_response.raise_for_status()
    return file_response.content, meta.json().get("mime_type", "application/octet-stream")


def dashboard_authorized():
    token = request.args.get("token", "")
    return bool(DASHBOARD_TOKEN and token and hmac.compare_digest(token, DASHBOARD_TOKEN))


def _normalize_choice(value):
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _match_choice(value, options, allow_unique_partial=False):
    key = _normalize_choice(value)
    if not key:
        return None
    exact = next((option for option in options if _normalize_choice(option) == key), None)
    if exact:
        return exact
    if allow_unique_partial:
        matches = [option for option in options if key in _normalize_choice(option) or _normalize_choice(option) in key]
        if len(matches) == 1:
            return matches[0]
    return None


def _infer_status(message, movement):
    text = _normalize_choice(message)
    is_income = str(movement or "").upper() in ("RECEITA", "ENTRADA", "E")
    if is_income:
        if re.search(r"\b(a receber|vou receber|irei receber|receberei|aguardando|pendente)\b", text):
            return "A receber"
        if re.search(r"\b(recebi|recebido|entrou|caiu|ganhei|vendi|faturei)\b", text):
            return "Recebido"
    else:
        if re.search(r"\b(a pagar|vou pagar|irei pagar|pagarei|preciso pagar|vencimento|vence|pendente)\b", text):
            return "A pagar"
        if re.search(r"\b(paguei|gastei|comprei|pago|paga|debitaram|descontou|apliquei|investi)\b", text):
            return "Pago"
    return None


def _resolve_account(transaction, options):
    accounts = options.get("accounts", [])
    if not accounts:
        return ""
    confidence = float(transaction.get("_ai_confidence") or 0)
    candidate = _match_choice(transaction.get("conta"), accounts)
    if candidate and confidence >= 0.55:
        return candidate
    raw = _normalize_choice(transaction.get("raw_message"))
    explicit = [option for option in accounts if _normalize_choice(option) and _normalize_choice(option) in raw]
    if len(explicit) == 1:
        return explicit[0]
    bank = _normalize_choice(transaction.get("banco"))
    matches = [option for option in accounts if bank and bank in _normalize_choice(option)]
    scope = str(transaction.get("tipo") or "PF").upper()
    markers = ("pj", "empresa", "empresas", "negocio") if scope == "PJ" else ("pf", "pessoal", "particular")
    scoped = [option for option in matches if any(marker in _normalize_choice(option).split() for marker in markers)]
    if scoped:
        matches = scoped
    if len(matches) == 1:
        return matches[0]
    scoped = [option for option in accounts if any(marker in _normalize_choice(option).split() for marker in markers)]
    if len(scoped) == 1:
        return scoped[0]
    if len(accounts) == 1:
        return accounts[0]
    return None


def _prepare_transaction(transaction):
    options = sheets.get_transaction_options()
    transaction["_options"] = options
    confidence = float(transaction.get("_ai_confidence") or 0)
    raw = _normalize_choice(transaction.get("raw_message"))

    category = _match_choice(transaction.get("categoria"), options.get("categories", []))
    category_is_generic = category and _normalize_choice(category) in ("outro", "outros", "outras despesas")
    category_was_said = category and _normalize_choice(category) in raw
    minimum_category_confidence = 0.75 if category_is_generic else 0.55
    if category and (category_was_said or confidence >= minimum_category_confidence):
        transaction["categoria"] = category
    else:
        transaction["_suggested_category"] = category
        transaction["categoria"] = ""
    if not options.get("categories") and not transaction.get("categoria"):
        transaction["categoria"] = "Outro"

    goals = options.get("goals", [])
    goal = _match_choice(transaction.get("meta"), goals)
    if goal and confidence >= 0.55:
        transaction["meta"] = goal
        transaction["_meta_confirmed"] = True
    elif goal and _normalize_choice(goal) in raw:
        transaction["meta"] = goal
        transaction["_meta_confirmed"] = True
    elif re.search(r"\b(sem meta|nenhuma meta|nenhum objetivo|nao se aplica)\b", raw):
        transaction["meta"] = ""
        transaction["_meta_confirmed"] = True
    else:
        transaction["meta"] = ""
        transaction["_meta_confirmed"] = not bool(goals)

    transaction["conta"] = _resolve_account(transaction, options) or ""
    is_income = str(transaction.get("movimento", "")).upper() in ("RECEITA", "ENTRADA", "E")
    valid_statuses = ("Recebido", "A receber") if is_income else ("Pago", "A pagar")
    status = _match_choice(transaction.get("status"), valid_statuses)
    if status and confidence >= 0.55:
        transaction["status"] = status
    else:
        transaction["status"] = _infer_status(transaction.get("raw_message"), transaction.get("movimento")) or ""
    if not transaction.get("descricao"):
        transaction["descricao"] = transaction.get("raw_message") or "Lançamento via WhatsApp"
    return transaction


def _transaction_question(transaction, field):
    options = transaction.get("_options", {})
    if field == "categoria":
        suggestion = transaction.get("_suggested_category")
        if suggestion:
            return f"Qual categoria devo usar? Minha sugestão é *{suggestion}*. Responda *SIM* para aceitar ou escreva outra categoria da lista."
        return "Qual categoria da lista da planilha melhor representa esse lançamento? Escreva o nome da categoria."
    if field == "meta":
        goals = options.get("goals", [])
        labels = " / ".join(goals) if goals else "nenhuma meta disponível"
        return f"A qual meta devo associar? Opções: {labels}. Responda *sem meta* se não se aplicar."
    if field == "conta":
        accounts = options.get("accounts", [])
        return "Qual conta devo usar? Responda com uma opção: " + " | ".join(accounts)
    if field == "status":
        statuses = options.get("statuses", [])
        is_income = str(transaction.get("movimento", "")).upper() in ("RECEITA", "ENTRADA", "E")
        relevant = [value for value in statuses if value in (("Recebido", "A receber") if is_income else ("Pago", "A pagar"))]
        return "Esse lançamento já foi realizado ou está pendente? Responda: " + " / ".join(relevant)
    if field == "confirmar":
        date = transaction.get("data") or "hoje"
        description = transaction.get("descricao") or transaction.get("raw_message") or "sem descrição"
        return (f"🔎 *Confere este lançamento?*\n{transaction.get('tipo')} · {transaction.get('movimento')} · R$ {float(transaction.get('valor') or 0):.2f} · {date}\n"
                f"Descrição: {description}\nCategoria: {transaction.get('categoria')} · Conta: {transaction.get('conta')}\n"
                f"Meta: {transaction.get('meta') or 'sem meta'} · Status: {transaction.get('status')}\n\n"
                "Responda *CONFIRMAR* para registrar ou *CANCELAR* para descartar.")
    return ""


def _save_transaction(transaction):
    return sheets.add_transaction(
        transaction.get("tipo") or "PF",
        transaction.get("movimento") or "DESPESA",
        transaction.get("categoria") or "Outro",
        transaction.get("descricao") or transaction.get("raw_message") or "Lançamento via WhatsApp",
        transaction.get("valor") or 0,
        phone_number=transaction.get("phone_number", ""),
        contact_name=transaction.get("contact_name", ""),
        message_id=transaction.get("message_id", ""),
        raw_message=transaction.get("raw_message", ""),
        transaction_date=transaction.get("data"),
        bank=transaction.get("banco") or "",
        payment_method=transaction.get("forma_pagamento") or "",
        account=transaction.get("conta") or "",
        meta=transaction.get("meta") or "",
        status=transaction.get("status") or "",
    )


def _advance_transaction(phone_number, transaction):
    if not transaction.get("categoria"):
        field = "categoria"
    elif not transaction.get("_meta_confirmed"):
        field = "meta"
    elif transaction.get("_options", {}).get("accounts") and not transaction.get("conta"):
        field = "conta"
    elif not transaction.get("status"):
        field = "status"
    elif transaction.get("needs_confirmation"):
        field = "confirmar"
    else:
        pending_transactions.pop(phone_number, None)
        success, message = _save_transaction(transaction)
        return message if success else message
    transaction["awaiting"] = field
    pending_transactions[phone_number] = transaction
    return _transaction_question(transaction, field)


def _start_transaction(phone_number, transaction):
    transaction["phone_number"] = phone_number
    transaction = _prepare_transaction(transaction)
    return _advance_transaction(phone_number, transaction)


def _continue_transaction(phone_number, reply):
    transaction = pending_transactions.get(phone_number)
    if not transaction:
        return None
    answer = _normalize_choice(reply)
    field = transaction.get("awaiting")
    if field == "confirmar":
        if answer in ("sim", "confirmar", "confirmo", "ok"):
            pending_transactions.pop(phone_number, None)
            success, message = _save_transaction(transaction)
            return message if success else message
        if answer in ("nao", "cancelar", "cancela"):
            pending_transactions.pop(phone_number, None)
            return "Tudo bem, não registrei esse lançamento."
        return "Responda *CONFIRMAR* para registrar ou *CANCELAR* para descartar."

    if answer in ("cancelar", "cancela"):
        pending_transactions.pop(phone_number, None)
        return "Tudo bem, não registrei esse lançamento."

    options = transaction.get("_options", {})
    if field == "categoria":
        suggestion = transaction.get("_suggested_category")
        choice = suggestion if answer in ("sim", "confirmo") and suggestion else _match_choice(reply, options.get("categories", []), allow_unique_partial=True)
        if not choice:
            return _transaction_question(transaction, field)
        transaction["categoria"] = choice
        transaction["_suggested_category"] = ""
    elif field == "meta":
        if answer in ("sem meta", "nenhuma", "nenhuma meta", "nenhum", "nenhum objetivo", "sem objetivo", "nao se aplica"):
            transaction["meta"] = ""
        else:
            choice = _match_choice(reply, options.get("goals", []), allow_unique_partial=True)
            if choice is None:
                return _transaction_question(transaction, field)
            transaction["meta"] = choice
        transaction["_meta_confirmed"] = True
    elif field == "conta":
        choice = _match_choice(reply, options.get("accounts", []), allow_unique_partial=True)
        if choice is None:
            return _transaction_question(transaction, field)
        transaction["conta"] = choice
    elif field == "status":
        is_income = str(transaction.get("movimento", "")).upper() in ("RECEITA", "ENTRADA", "E")
        valid_statuses = ("Recebido", "A receber") if is_income else ("Pago", "A pagar")
        choice = _match_choice(reply, valid_statuses) or _infer_status(reply, transaction.get("movimento"))
        if choice is None:
            return _transaction_question(transaction, field)
        transaction["status"] = choice
    else:
        pending_transactions.pop(phone_number, None)
        return "Não consegui continuar esse lançamento. Envie a mensagem novamente."
    return _advance_transaction(phone_number, transaction)


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge", "")
    if mode == "subscribe" and token == VERIFY_TOKEN and VERIFY_TOKEN:
        return challenge, 200
    return "Invalid verification token", 403


@app.route("/webhook", methods=["POST"])
def handle_message():
    if not is_valid_signature():
        return jsonify({"error": "Invalid webhook signature"}), 401

    data = request.get_json(silent=True) or {}
    if data.get("object") != "whatsapp_business_account":
        return jsonify({"status": "ignored"}), 200

    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue
                value = change.get("value", {})
                contacts = {
                    contact.get("wa_id"): contact.get("profile", {}).get("name", "")
                    for contact in value.get("contacts", [])
                }
                for message in value.get("messages", []):
                    from_number = message.get("from", "")
                    message_id = message.get("id", "")
                    if message_id:
                        with received_message_lock:
                            if message_id in received_message_ids:
                                print(f"[Webhook] mensagem duplicada ignorada: {message_id}")
                                continue
                            received_message_ids.add(message_id)
                            if len(received_message_ids) > 10000:
                                received_message_ids.clear()
                    message_type = message.get("type")
                    if message_type != "text":
                        if message_type in ("audio", "image", "document"):
                            Thread(target=process_media_message, args=(from_number, message, contacts.get(from_number, ""), message_id), daemon=True).start()
                        continue
                    message_text = message.get("text", {}).get("body", "").strip()
                    if message_text:
                        Thread(target=process_user_message, args=(from_number, message_text, contacts.get(from_number, ""), message_id), daemon=True).start()
        return jsonify({"status": "ok"}), 200
    except Exception as error:
        print(f"[Webhook] erro ao processar mensagem: {error}")
        return jsonify({"error": "processing failed"}), 500


def process_media_message(phone_number, message, contact_name="", message_id=""):
    if not gemini.configured:
        whatsapp.send_message(phone_number, "📎 Recebi o arquivo, mas a análise ainda não está configurada. Envie o lançamento por texto por enquanto.")
        return
    try:
        media = message.get(message.get("type"), {})
        media_bytes, mime_type = download_whatsapp_media(media.get("id"))
        profile = sheets.get_profile(phone_number, contact_name)
        categories = sheets.get_categories()
        options = sheets.get_transaction_options()
        result, error = gemini.extract_transaction(media_bytes, mime_type, profile["context"], categories, options)
        if error or not result:
            whatsapp.send_message(phone_number, f"📎 {error or 'Não consegui ler o arquivo.'}")
            return
        caption = str(media.get("caption") or "").strip()
        raw_message = caption or result.get("transcricao") or result.get("descricao") or "Mídia enviada pelo WhatsApp"
        transaction = {
            "tipo": result.get("tipo") or profile["context"],
            "movimento": result.get("movimento") or "DESPESA",
            "valor": result.get("valor"),
            "data": result.get("data"),
            "categoria": result.get("categoria"),
            "conta": result.get("conta"),
            "meta": result.get("meta"),
            "status": result.get("status"),
            "descricao": result.get("descricao") or result.get("estabelecimento") or "Lançamento via mídia",
            "raw_message": str(raw_message),
            "banco": result.get("banco") or "",
            "forma_pagamento": result.get("forma_pagamento") or "",
            "_ai_confidence": result.get("confidence", 0),
            "contact_name": contact_name,
            "message_id": message_id,
            "needs_confirmation": True,
        }
        whatsapp.send_message(phone_number, _start_transaction(phone_number, transaction))
    except Exception as error:
        print(f"[Media] erro ao processar arquivo: {error}")
        whatsapp.send_message(phone_number, "📎 Recebi o arquivo, mas não consegui analisá-lo. Envie uma foto mais nítida ou uma mensagem de texto.")


def process_user_message(phone_number, message_text, contact_name="", message_id=""):
    message_upper = message_text.upper().strip()
    pending_response = _continue_transaction(phone_number, message_text)
    if pending_response is not None:
        whatsapp.send_message(phone_number, pending_response)
        return
    response = None
    profile = sheets.get_profile(phone_number, contact_name)

    if message_upper in ("SIM", "CONFIRMAR", "CONFIRMO") and phone_number in pending_deletions:
        transaction = pending_deletions.pop(phone_number)
        response = "✅ Lançamento apagado." if sheets.delete_transaction(transaction["row"]) else "❌ Não consegui apagar o lançamento."
    elif message_upper in ("NÃO", "NAO", "CANCELAR") and phone_number in pending_deletions:
        pending_deletions.pop(phone_number, None)
        response = "Tudo bem, não apaguei nada."
    elif message_upper in ("MEU PERFIL", "PERFIL", "CONFIGURAÇÕES", "CONFIGURACOES"):
        response = f"👤 *Seu perfil*\nNome: {profile['name']}\nContexto atual: {profile['context']}\nTipo padrão: {profile['default_type']}\n\nEnvie *usar PF* ou *usar PJ* para trocar."
    elif message_upper in ("USAR PF", "PF"):
        sheets.set_profile_context(phone_number, "PF", contact_name)
        response = "✅ Contexto alterado para *PF*. Agora mensagens sem indicação usarão PF."
    elif message_upper in ("USAR PJ", "PJ"):
        sheets.set_profile_context(phone_number, "PJ", contact_name)
        response = "✅ Contexto alterado para *PJ*. Agora mensagens sem indicação usarão PJ."

    if message_upper == "AJUDA":
        response = whatsapp.get_help_message()
    elif message_upper in ("CATEGORIAS", "CATEGORIA", "LISTA DE CATEGORIAS"):
        response = get_categories_text(sheets.get_categories())
    elif message_upper.startswith("CATEGORIA "):
        parts = message_text.split(None, 4)
        if len(parts) < 5 or parts[1].lower() not in ("adicionar", "remover"):
            response = "Use: *categoria adicionar PF despesa Mercado* ou *categoria remover PF despesa Mercado*"
        else:
            tipo = parts[2].upper()
            movimento = parts[3].upper()
            if movimento == "INVESTIMENTO":
                tipo = "GERAL"
            _, response = sheets.update_category(parts[1].lower(), tipo, movimento, parts[4])
    elif message_upper.startswith("RESUMO"):
        tipo = "PJ" if "PJ" in message_upper else "PF" if "PF" in message_upper else profile["context"]
        summary = sheets.get_summary(tipo)
        response = sheets.format_summary(summary, tipo) if summary else "Ainda não há lançamentos para esse contexto."
    elif message_upper in ("SALDO", "MEU SALDO", "QUANTO TENHO"):
        summary = sheets.get_summary(profile["context"])
        response = sheets.format_summary(summary, profile["context"]) if summary else "Ainda não há lançamentos para esse contexto."
    elif message_upper.startswith(("BUSCAR ", "PROCURAR ", "PESQUISAR ")) or "ONDE GASTEI" in message_upper:
        keyword = message_text.split(None, 1)[1] if " " in message_text else None
        if "ONDE GASTEI" in message_upper:
            keyword = None
        rows = sheets.find_transactions(tipo=profile["context"], keyword=keyword, phone_number=phone_number)
        if not rows:
            response = "Não encontrei lançamentos para essa busca."
        else:
            response = "🔎 *Lançamentos encontrados:*\n" + "\n".join(f"• {r['date']} — R$ {r['value']} — {r['category']} — {r['description']}" for r in rows)
    elif message_upper.startswith(("APAGAR ÚLTIMO", "APAGAR ULTIMO", "CORRIGIR ÚLTIMO", "CORRIGIR ULTIMO")):
        rows = sheets.find_transactions(tipo=profile["context"], phone_number=phone_number, limit=1)
        if not rows:
            response = "Não encontrei um lançamento recente para alterar."
        elif message_upper.startswith("APAGAR"):
            pending_deletions[phone_number] = rows[0]
            response = f"⚠️ Apagar este lançamento?\nR$ {rows[0]['value']} — {rows[0]['description']}\nResponda *SIM* para confirmar ou *NÃO* para cancelar."
        else:
            value, remaining = whatsapp._extract_amount(message_text)
            if value is None:
                response = "Use: *corrigir último para R$ 75* ou *corrigir último para R$ 75 almoço*."
            else:
                description = re.sub(r"\b(corrigir|último|ultimo|para|r\$)\b", " ", remaining, flags=re.I).strip(" -:")
                ok = sheets.update_transaction(rows[0]["row"], value=value, description=description or None)
                response = "✅ Último lançamento corrigido." if ok else "❌ Não consegui corrigir o lançamento."
    elif response is None:
        categories = sheets.get_categories()
        transaction_options = sheets.get_transaction_options()
        interpreted = None
        if gemini.configured:
            interpreted, _ = gemini.interpret_text(message_text, profile["context"], categories, transaction_options)
        confidence = float((interpreted or {}).get("confidence") or 0)
        intent = interpreted.get("intent") if interpreted and confidence >= 0.55 else None

        if intent == "launch" and (interpreted.get("valor") or 0) > 0:
            parsed = whatsapp.parse_message(message_text, categories=categories, default_type=profile["context"])
            if not parsed[-1]:
                tipo_pj_pf, tipo_movimento, categoria, valor, descricao, _ = parsed
            else:
                tipo_pj_pf, tipo_movimento, categoria, valor, descricao = profile["context"], "DESPESA", "", float(interpreted["valor"]), interpreted.get("descricao") or ""
            bank, payment = whatsapp.extract_payment_details(message_text)
            transaction = {
                "tipo": interpreted.get("tipo") if interpreted.get("tipo") in ("PF", "PJ") else tipo_pj_pf,
                "movimento": interpreted.get("movimento") or tipo_movimento,
                "valor": valor or interpreted.get("valor"),
                "data": interpreted.get("data") or whatsapp.extract_date(message_text),
                "categoria": interpreted.get("categoria") or categoria,
                "conta": interpreted.get("conta"),
                "meta": interpreted.get("meta"),
                "status": interpreted.get("status"),
                "descricao": interpreted.get("descricao") or descricao,
                "raw_message": message_text,
                "banco": interpreted.get("banco") or bank,
                "forma_pagamento": interpreted.get("forma_pagamento") or payment,
                "_ai_confidence": confidence,
                "contact_name": contact_name,
                "message_id": message_id,
            }
            response = _start_transaction(phone_number, transaction)
        elif intent in ("summary", "balance"):
            tipo = interpreted.get("tipo") if interpreted.get("tipo") in ("PF", "PJ") else profile["context"]
            summary = sheets.get_summary(tipo)
            response = sheets.format_summary(summary, tipo) if summary else "Ainda não há lançamentos para esse contexto."
        elif intent == "profile":
            response = f"👤 *Seu perfil*\nNome: {profile['name']}\nContexto atual: {profile['context']}\nTipo padrão: {profile['default_type']}\n\nVocê pode dizer, por exemplo: *quero usar PJ*."
        elif intent == "categories":
            response = get_categories_text(categories)
        elif intent == "switch_context":
            context = interpreted.get("contexto") if interpreted.get("contexto") in ("PF", "PJ") else profile["context"]
            sheets.set_profile_context(phone_number, context, contact_name)
            response = f"✅ Contexto alterado para *{context}*. Os próximos lançamentos sem indicação usarão esse contexto."
        elif intent == "help":
            response = whatsapp.get_help_message()
        elif intent == "search":
            rows = sheets.find_transactions(tipo=profile["context"], keyword=interpreted.get("keyword") or None, phone_number=phone_number)
            response = "Não encontrei lançamentos para essa busca." if not rows else "🔎 *Encontrei:*\n" + "\n".join(f"• {r['date']} — R$ {r['value']} — {r['category']} — {r['description']}" for r in rows)
        else:
            tipo_pj_pf, tipo_movimento, categoria, valor, descricao, erro = whatsapp.parse_message(message_text, categories=categories, default_type=profile["context"])
            if erro:
                response = erro + "\n\nVocê pode escrever de forma livre, como: *gastei 45 no almoço*."
            else:
                transaction_date = whatsapp.extract_date(message_text)
                bank, payment = whatsapp.extract_payment_details(message_text)
                transaction = {
                    "tipo": tipo_pj_pf,
                    "movimento": tipo_movimento,
                    "valor": valor,
                    "data": transaction_date,
                    "categoria": categoria,
                    "conta": (interpreted or {}).get("conta"),
                    "meta": (interpreted or {}).get("meta"),
                    "status": (interpreted or {}).get("status"),
                    "descricao": descricao,
                    "raw_message": message_text,
                    "banco": (interpreted or {}).get("banco") or bank,
                    "forma_pagamento": (interpreted or {}).get("forma_pagamento") or payment,
                    "_ai_confidence": confidence,
                    "contact_name": contact_name,
                    "message_id": message_id,
                }
                response = _start_transaction(phone_number, transaction)

    if response:
        whatsapp.send_message(phone_number, response)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "sheets_connected": sheets.service is not None,
        "spreadsheet_configured": bool(sheets.spreadsheet_id),
        "whatsapp_configured": bool(whatsapp.access_token and whatsapp.phone_number_id),
        "gemini_configured": gemini.configured,
        "webhook_signature_configured": bool(APP_SECRET),
        "sheet_name": sheets.get_sheet_by_month(),
    })


@app.route("/api/whatsapp/status", methods=["GET"])
def status():
    return jsonify({
        "status": "running",
        "webhook_path": "/webhook",
        "sheets_connected": sheets.service is not None,
        "spreadsheet_configured": bool(sheets.spreadsheet_id),
        "whatsapp_configured": bool(whatsapp.access_token and whatsapp.phone_number_id),
        "webhook_signature_configured": bool(APP_SECRET),
        "sheet_name": sheets.get_sheet_by_month(),
    })


@app.route("/dashboard", methods=["GET"])
def dashboard():
    if not DASHBOARD_TOKEN:
        return "Dashboard ainda não configurado: adicione DASHBOARD_TOKEN no Render.", 503
    if not dashboard_authorized():
        return "Acesso negado. Abra o dashboard com o token fornecido pelo administrador.", 401
    return render_template("dashboard.html")


@app.route("/api/dashboard", methods=["GET"])
def dashboard_api():
    if not DASHBOARD_TOKEN or not dashboard_authorized():
        return jsonify({"error": "Acesso não autorizado."}), 401
    month = request.args.get("month", "todos").strip()
    tipo = request.args.get("tipo", "TODOS").strip().upper()
    if tipo not in ("TODOS", "PF", "PJ"):
        tipo = "TODOS"
    return jsonify(sheets.get_dashboard_data(month=month, tipo=tipo))


@app.route("/api/admin/sync-layout", methods=["GET", "POST"])
def sync_layout_api():
    token = request.headers.get("X-Layout-Sync-Token", "")
    if not LAYOUT_SYNC_TOKEN or not hmac.compare_digest(token, LAYOUT_SYNC_TOKEN):
        return jsonify({"error": "Não autorizado."}), 401
    if request.method == "GET":
        return jsonify(layout_sync_status)
    if layout_sync_status["state"] == "running":
        return jsonify(layout_sync_status), 202

    def run_sync():
        layout_sync_status.update({"state": "running"})
        try:
            sheets.sync_cashflow_tabs_from_august()
            layout_sync_status.update({"state": "completed"})
        except Exception as error:
            print(f"[Layout] erro na sincronização: {error}")
            layout_sync_status.update({"state": "failed"})

    Thread(target=run_sync, daemon=True).start()
    return jsonify({"state": "running"}), 202


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
