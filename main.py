import hashlib
import hmac
import os
import re

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from categories import get_categories_text
from sheets_handler import SheetsHandler
from whatsapp_handler import WhatsAppHandler

load_dotenv()

app = Flask(__name__)

whatsapp = WhatsAppHandler(
    phone_number_id=os.getenv("PHONE_NUMBER_ID"),
    access_token=os.getenv("ACCESS_TOKEN"),
)
sheets = SheetsHandler()
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
APP_SECRET = os.getenv("APP_SECRET", "")
pending_deletions = {}


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
                    message_type = message.get("type")
                    if message_type != "text":
                        if message_type in ("audio", "image", "document"):
                            whatsapp.send_message(from_number, "📎 Recebi seu arquivo. A leitura de áudio/comprovante será ativada na próxima versão; por enquanto, envie também uma descrição em texto.")
                        continue
                    message_text = message.get("text", {}).get("body", "").strip()
                    if message_text:
                        process_user_message(from_number, message_text, contacts.get(from_number, ""), message.get("id", ""))
        return jsonify({"status": "ok"}), 200
    except Exception as error:
        print(f"[Webhook] erro ao processar mensagem: {error}")
        return jsonify({"error": "processing failed"}), 500


def process_user_message(phone_number, message_text, contact_name="", message_id=""):
    message_upper = message_text.upper().strip()
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
    else:
        tipo_pj_pf, tipo_movimento, categoria, valor, descricao, erro = whatsapp.parse_message(
            message_text, categories=sheets.get_categories(), default_type=profile["context"]
        )
        if erro:
            response = erro
        else:
            sucesso, mensagem = sheets.add_transaction(
                tipo_pj_pf, tipo_movimento, categoria, descricao, valor,
                phone_number=phone_number,
                contact_name=contact_name,
                message_id=message_id,
                raw_message=message_text,
            )
            if sucesso:
                response = mensagem
            else:
                response = mensagem

    if response:
        whatsapp.send_message(phone_number, response)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "sheets_connected": sheets.service is not None,
        "spreadsheet_configured": bool(sheets.spreadsheet_id),
        "whatsapp_configured": bool(whatsapp.access_token and whatsapp.phone_number_id),
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


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
