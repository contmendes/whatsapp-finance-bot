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


def _send_whatsapp_reply(phone_number, message, source):
    try:
        if not whatsapp.send_message(phone_number, message):
            print(f"[WhatsApp] não foi possível enviar resposta (origem={source})")
    except Exception as error:
        print(f"[WhatsApp] exceção ao enviar resposta (origem={source}): {error}")


def _run_message_worker(processor, args, message_id):
    try:
        processor(*args)
    except Exception as error:
        print(f"[Webhook] falha no processamento em segundo plano: {error}")
    finally:
        if message_id:
            with received_message_lock:
                received_message_ids.discard(message_id)


def is_valid_signature() -> bool:
    if not APP_SECRET:
        print("[Webhook] APP_SECRET não configurado")
        return False
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature.startswith("sha256="):
        print("[Webhook] assinatura ausente ou inválida")
        return False
    expected = "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"), request.get_data(cache=True), hashlib.sha256
    ).hexdigest()
    valid = hmac.compare_digest(signature, expected)
    if not valid:
        print("[Webhook] assinatura inválida")
    return valid


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


def _scope_from_text(value):
    text = _normalize_choice(value)
    if re.search(r"\b(pj|cnpj|empresa|empresas|negocio|negocios)\b", text):
        return "PJ"
    if re.search(r"\b(pf|pessoal|particular|pessoa fisica)\b", text):
        return "PF"
    return None


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


def _match_payment_option(value, options, account=""):
    key = _normalize_choice(value)
    if not key:
        return None
    exact = _match_choice(value, options)
    account_key = _normalize_choice(account)
    if exact and (not account_key or account_key in _normalize_choice(exact)):
        return exact
    if exact and account_key:
        return None
    candidates = [option for option in options if key in _normalize_choice(option)]
    if account_key:
        candidates = [option for option in candidates if account_key in _normalize_choice(option)]
    return candidates[0] if len(candidates) == 1 else None


def _match_account_option(value, options):
    exact = _match_choice(value, options)
    if exact:
        return exact
    key = _normalize_choice(value)
    if not key:
        return None
    candidates = [option for option in options if key in _normalize_choice(option)]
    return candidates[0] if len(candidates) == 1 else None


def _payment_option_name(method, account=""):
    method = " ".join(str(method or "").split())
    account = " ".join(str(account or "").split())
    if account and _normalize_choice(account) not in _normalize_choice(method):
        return f"{method} {account}".strip()
    return method


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
    raw = _normalize_choice(transaction.get("raw_message"))
    explicit = [option for option in accounts if _normalize_choice(option) and _normalize_choice(option) in raw]
    if len(explicit) == 1:
        return explicit[0]
    bank = _normalize_choice(transaction.get("banco"))
    matches = [option for option in accounts if bank and bank in _normalize_choice(option)]
    scope = str(transaction.get("tipo") or "").upper()
    if scope not in ("PF", "PJ"):
        scope = ""
    markers = ("pj", "empresa", "empresas", "negocio") if scope == "PJ" else ("pf", "pessoal", "particular")
    if bank:
        if not matches:
            return None
        if scope:
            scoped = [option for option in matches if any(marker in _normalize_choice(option).split() for marker in markers)]
            if scoped:
                matches = scoped
        return matches[0] if len(matches) == 1 else None
    return None


def _prepare_transaction(transaction):
    options = sheets.get_transaction_options()
    transaction["_options"] = options
    confidence = float(transaction.get("_ai_confidence") or 0)
    raw = _normalize_choice(transaction.get("raw_message"))
    scope = transaction.get("tipo") if transaction.get("tipo") in ("PF", "PJ") else _scope_from_text(transaction.get("raw_message"))
    transaction["tipo"] = scope

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
    bank = str(transaction.get("banco") or "").strip()
    if bank and not transaction["conta"]:
        bank_key = _normalize_choice(bank)
        bank_matches = [option for option in options.get("accounts", []) if bank_key and bank_key in _normalize_choice(option)]
        if not bank_matches:
            transaction["_new_account_candidate"] = bank
    payment_method = str(transaction.get("forma_pagamento") or "").strip()
    if payment_method:
        payment = _match_payment_option(payment_method, options.get("payment_methods", []), transaction["conta"])
        if payment:
            transaction["forma_pagamento"] = payment
            transaction["_suggested_payment_method"] = ""
        else:
            transaction["forma_pagamento"] = ""
            transaction["_suggested_payment_method"] = payment_method
    else:
        transaction["forma_pagamento"] = ""
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
    if field == "tipo":
        return "Esse lançamento é de Pessoa Física ou da empresa? Responda *PF* ou *PJ*."
    if field == "categoria":
        suggestion = transaction.get("_suggested_category")
        if suggestion:
            return (f"Qual categoria devo usar? Minha sugestão é *{suggestion}*. Responda *SIM* para aceitar, "
                    "ou escreva outra categoria. Se ela ainda não existir, vou adicioná-la ao plano de contas.")
        return ("Qual categoria devo usar? Escreva o nome. Se ainda não existir na planilha, "
                "vou adicioná-la ao plano de contas e à lista de categorias.")
    if field == "meta":
        goals = options.get("goals", [])
        labels = " / ".join(goals) if goals else "nenhuma meta disponível"
        return f"A qual meta devo associar? Opções: {labels}. Responda *sem meta* se não se aplicar."
    if field == "conta":
        accounts = options.get("accounts", [])
        new_account = transaction.get("_new_account_candidate")
        if new_account:
            return (f"Não encontrei *{new_account}* no menu de contas. Envie o nome como deseja que apareça "
                    "na planilha (por exemplo, *C6 Pessoal* ou *C6 Empresa*) e vou adicioná-lo."
                    + (" Opções atuais: " + " | ".join(accounts) if accounts else ""))
        return "Qual conta devo usar? Responda com uma opção: " + " | ".join(accounts) + "\nSe nenhuma servir, escreva o nome da nova conta para adicioná-la."
    if field == "forma_pagamento":
        suggestion = transaction.get("_suggested_payment_method") or ""
        account = transaction.get("conta") or ""
        proposed = _payment_option_name(suggestion, account)
        if suggestion:
            exact = _match_choice(suggestion, options.get("payment_methods", []))
            account_key = _normalize_choice(account)
            if exact and account_key and account_key not in _normalize_choice(exact):
                available = [method for method in options.get("payment_methods", [])
                             if account_key in _normalize_choice(method)]
                return (f"A opção *{exact}* está vinculada a outra conta, não a *{account}*. "
                        "Escolha uma forma de pagamento compatível"
                        + (": " + " | ".join(available) if available else "")
                        + ", ou escreva o nome de uma nova forma para adicioná-la.")
            return (f"A forma de pagamento *{suggestion}* não está disponível para essa conta. "
                    f"Responda *SIM* para adicioná-la como *{proposed}* ou escreva outra forma de pagamento.")
        methods = options.get("payment_methods", [])
        return "Qual foi a forma de pagamento? Opções: " + " | ".join(methods) + "\nSe não houver, escreva o nome para adicioná-lo."
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
        category_added=bool(transaction.get("_category_added")),
    )


def _commit_pending_transaction(phone_number, transaction):
    success, message = _save_transaction(transaction)
    if success:
        pending_transactions.pop(phone_number, None)
        return message
    transaction["awaiting"] = "retry_save"
    pending_transactions[phone_number] = transaction
    return f"{message}\n\nO lançamento continua pendente. Responda *TENTAR NOVAMENTE* para tentar salvar de novo ou *CANCELAR* para descartar."


def _advance_transaction(phone_number, transaction):
    if transaction.get("tipo") not in ("PF", "PJ"):
        field = "tipo"
    elif not transaction.get("categoria"):
        field = "categoria"
    elif not transaction.get("_meta_confirmed"):
        field = "meta"
    elif not transaction.get("conta") and (transaction.get("_options", {}).get("accounts") or transaction.get("banco")):
        field = "conta"
    elif transaction.get("_suggested_payment_method"):
        field = "forma_pagamento"
    elif not transaction.get("status"):
        field = "status"
    elif transaction.get("needs_confirmation"):
        field = "confirmar"
    else:
        return _commit_pending_transaction(phone_number, transaction)
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
    if field == "retry_save":
        if answer in ("tentar novamente", "repetir", "sim", "salvar"):
            return _commit_pending_transaction(phone_number, transaction)
        return "Responda *TENTAR NOVAMENTE* para tentar salvar ou *CANCELAR* para descartar."
    if field == "confirmar":
        if answer in ("sim", "confirmar", "confirmo", "ok"):
            return _commit_pending_transaction(phone_number, transaction)
        if answer in ("nao", "cancelar", "cancela"):
            pending_transactions.pop(phone_number, None)
            return "Tudo bem, não registrei esse lançamento."
        return "Responda *CONFIRMAR* para registrar ou *CANCELAR* para descartar."

    if answer in ("cancelar", "cancela"):
        pending_transactions.pop(phone_number, None)
        return "Tudo bem, não registrei esse lançamento."

    options = transaction.get("_options", {})
    if field == "tipo":
        scope = _scope_from_text(reply)
        if not scope:
            return _transaction_question(transaction, field)
        transaction["tipo"] = scope
        transaction["conta"] = _resolve_account(transaction, options) or ""
        if transaction.get("banco") and not transaction["conta"]:
            bank_key = _normalize_choice(transaction["banco"])
            if not any(bank_key and bank_key in _normalize_choice(option) for option in options.get("accounts", [])):
                transaction["_new_account_candidate"] = transaction["banco"]
        return _advance_transaction(phone_number, transaction)
    elif field == "categoria":
        suggestion = transaction.get("_suggested_category")
        choice = suggestion if answer in ("sim", "confirmo") and suggestion else _match_choice(reply, options.get("categories", []), allow_unique_partial=True)
        category_created = False
        if not choice:
            if answer in ("sim", "confirmo", "nao", "nao sei", "nao tenho") or answer.startswith("nao "):
                return _transaction_question(transaction, field)
            added, category_name, category_created, error_message = sheets.add_category_to_plan(
                reply, transaction.get("tipo") or "PF", transaction.get("movimento") or "DESPESA"
            )
            if not added:
                return f"{error_message}\n\n" + _transaction_question(transaction, field)
            choice = category_name
            # O nome foi encontrado ou gravado na fonte que alimenta a validação da planilha.
            transaction["_category_added"] = True
        transaction["categoria"] = choice
        transaction["_suggested_category"] = ""
        next_response = _advance_transaction(phone_number, transaction)
        if category_created:
            return f"✅ Adicionei *{choice}* ao plano de contas e à lista de categorias.\n\n{next_response}"
        return next_response
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
        choice = _match_account_option(reply, options.get("accounts", []))
        added = False
        if choice is None:
            if answer in ("nao", "não", "nao sei", "nao tenho") or answer.startswith("nao "):
                return _transaction_question(transaction, field)
            new_name = transaction.get("_new_account_candidate", "") if answer in ("sim", "confirmo", "adicionar") else " ".join(str(reply).split())
            if answer in ("sim", "confirmo", "adicionar") and new_name:
                new_name = _payment_option_name(new_name, "Pessoal" if transaction.get("tipo") == "PF" else "Empresa")
            if not new_name:
                return _transaction_question(transaction, field)
            ok, account_name, added, error_message = sheets.add_account_option(new_name)
            if not ok:
                return f"{error_message}\n\n" + _transaction_question(transaction, field)
            choice = account_name
            transaction["_account_added"] = added
        transaction["conta"] = choice
        transaction["_new_account_candidate"] = ""
        payment_candidate = transaction.get("_suggested_payment_method")
        if payment_candidate:
            payment = _match_payment_option(payment_candidate, options.get("payment_methods", []), choice)
            if payment:
                transaction["forma_pagamento"] = payment
                transaction["_suggested_payment_method"] = ""
        next_response = _advance_transaction(phone_number, transaction)
        if added:
            return f"✅ Adicionei a conta *{choice}* ao menu.\n\n{next_response}"
        return next_response
    elif field == "forma_pagamento":
        suggestion = transaction.get("_suggested_payment_method") or ""
        if answer in ("nao", "não", "nao sei", "nao tenho") or answer.startswith("nao "):
            return _transaction_question(transaction, field)
        method = suggestion if answer in ("sim", "confirmo", "adicionar") and suggestion else " ".join(str(reply).split())
        exact_menu_choice = _match_choice(method, options.get("payment_methods", []))
        account_key = _normalize_choice(transaction.get("conta"))
        if exact_menu_choice and account_key and account_key not in _normalize_choice(exact_menu_choice):
            return _transaction_question(transaction, field)
        choice = _match_payment_option(method, options.get("payment_methods", []), transaction.get("conta"))
        added = False
        if choice is None:
            option_name = _payment_option_name(method, transaction.get("conta"))
            if not option_name:
                return _transaction_question(transaction, field)
            ok, payment_name, added, error_message = sheets.add_payment_method_option(option_name)
            if not ok:
                return f"{error_message}\n\n" + _transaction_question(transaction, field)
            choice = payment_name
            transaction["_payment_added"] = added
        transaction["forma_pagamento"] = choice
        transaction["_suggested_payment_method"] = ""
        next_response = _advance_transaction(phone_number, transaction)
        if added:
            return f"✅ Adicionei *{choice}* ao menu de formas de pagamento.\n\n{next_response}"
        return next_response
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
                    print(f"[Webhook] mensagem recebida; tipo={message_type or 'desconhecido'}")
                    if message_type != "text":
                        if message_type in ("audio", "image", "document"):
                            Thread(target=_run_message_worker, args=(process_media_message, (from_number, message, contacts.get(from_number, ""), message_id), message_id), daemon=True).start()
                        elif message_id:
                            with received_message_lock:
                                received_message_ids.discard(message_id)
                        continue
                    message_text = message.get("text", {}).get("body", "").strip()
                    if message_text:
                        Thread(target=_run_message_worker, args=(process_user_message, (from_number, message_text, contacts.get(from_number, ""), message_id), message_id), daemon=True).start()
                    elif message_id:
                        with received_message_lock:
                            received_message_ids.discard(message_id)
        return jsonify({"status": "ok"}), 200
    except Exception as error:
        print(f"[Webhook] erro ao processar mensagem: {error}")
        return jsonify({"error": "processing failed"}), 500


def process_media_message(phone_number, message, contact_name="", message_id=""):
    if not gemini.configured:
        _send_whatsapp_reply(phone_number, "📎 Recebi o arquivo, mas a análise ainda não está configurada. Envie o lançamento por texto por enquanto.", "mídia")
        return
    try:
        media = message.get(message.get("type"), {})
        media_bytes, mime_type = download_whatsapp_media(media.get("id"))
        profile = sheets.get_profile(phone_number, contact_name)
        categories = sheets.get_categories()
        options = sheets.get_transaction_options()
        result, error = gemini.extract_transaction(media_bytes, mime_type, profile["context"], categories, options)
        if error or not result:
            _send_whatsapp_reply(phone_number, f"📎 {error or 'Não consegui ler o arquivo.'}", "mídia")
            return
        caption = str(media.get("caption") or "").strip()
        raw_message = caption or result.get("transcricao") or result.get("descricao") or "Mídia enviada pelo WhatsApp"
        transaction = {
            "tipo": result.get("tipo") if result.get("tipo") in ("PF", "PJ") else _scope_from_text(raw_message),
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
        _send_whatsapp_reply(phone_number, _start_transaction(phone_number, transaction), "mídia")
    except Exception as error:
        print(f"[Media] erro ao processar arquivo: {error}")
        _send_whatsapp_reply(phone_number, "📎 Recebi o arquivo, mas não consegui analisá-lo. Envie uma foto mais nítida ou uma mensagem de texto.", "mídia")


def process_user_message(phone_number, message_text, contact_name="", message_id=""):
    message_upper = message_text.upper().strip()
    pending_response = _continue_transaction(phone_number, message_text)
    if pending_response is not None:
        _send_whatsapp_reply(phone_number, pending_response, "conversa pendente")
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
        response = "✅ Contexto do perfil alterado para *PF*. Se um lançamento não deixar claro se é pessoal ou da empresa, vou perguntar antes de registrar."
    elif message_upper in ("USAR PJ", "PJ"):
        sheets.set_profile_context(phone_number, "PJ", contact_name)
        response = "✅ Contexto do perfil alterado para *PJ*. Se um lançamento não deixar claro se é pessoal ou da empresa, vou perguntar antes de registrar."

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
                "tipo": _scope_from_text(message_text),
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
            response = f"✅ Contexto do perfil alterado para *{context}*. Se um lançamento não deixar claro o contexto, vou perguntar antes de registrar."
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
                    "tipo": _scope_from_text(message_text),
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
        _send_whatsapp_reply(phone_number, response, "texto")


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
