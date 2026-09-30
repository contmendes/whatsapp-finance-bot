import hashlib
import hmac
import os

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
                    if message.get("type") != "text":
                        continue
                    from_number = message.get("from", "")
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

    if message_upper == "AJUDA":
        response = whatsapp.get_help_message()
    elif message_upper == "CATEGORIAS":
        response = get_categories_text()
    elif message_upper.startswith("RESUMO"):
        tipo = "PJ" if "PJ" in message_upper else "PF" if "PF" in message_upper else None
        if not tipo:
            response = "Use: 'Resumo PJ' ou 'Resumo PF'"
        else:
            summary = sheets.get_summary(tipo)
            response = sheets.format_summary(summary, tipo) if summary else "Ainda não há lançamentos para esse contexto."
    else:
        tipo_pj_pf, tipo_movimento, categoria, valor, descricao, erro = whatsapp.parse_message(message_text)
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
