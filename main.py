from flask import Flask, request, jsonify
import os
from dotenv import load_dotenv
from whatsapp_handler import WhatsAppHandler
from sheets_handler import SheetsHandler
from categories import get_categories_text

# Carregar variáveis de ambiente
load_dotenv()

app = Flask(__name__)

# Inicializar handlers
whatsapp = WhatsAppHandler(
    phone_number_id=os.getenv('PHONE_NUMBER_ID'),
    access_token=os.getenv('ACCESS_TOKEN')
)

sheets = SheetsHandler()

# Token de verificação (gerar um aleatório)
VERIFY_TOKEN = os.getenv('VERIFY_TOKEN', 'seu_token_seguro_aqui')

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """Verifica o webhook do WhatsApp"""
    verify_token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if verify_token == VERIFY_TOKEN:
        return challenge
    else:
        return 'Invalid verification token', 403

@app.route('/webhook', methods=['POST'])
def handle_message():
    """Recebe e processa mensagens do WhatsApp"""
    try:
        data = request.get_json()

        # Verificar se é uma mensagem
        if data.get('object') == 'whatsapp_business_account':
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    if change.get('field') == 'messages':
                        messages = change.get('value', {}).get('messages', [])

                        for message in messages:
                            from_number = message.get('from')
                            message_text = message.get('text', {}).get('body', '').strip()

                            if message_text:
                                process_user_message(from_number, message_text)

        return jsonify({'status': 'ok'})

    except Exception as e:
        print(f"❌ Erro ao processar webhook: {e}")
        return jsonify({'error': str(e)}), 500

def process_user_message(phone_number, message_text):
    """Processa mensagem do usuário e responde"""

    message_upper = message_text.upper().strip()
    response = None

    # Comandos especiais
    if message_upper == 'AJUDA':
        response = whatsapp.get_help_message()

    elif message_upper == 'CATEGORIAS':
        response = get_categories_text()

    elif message_upper.startswith('RESUMO'):
        # Tentar extrair PJ ou PF
        if 'PJ' in message_upper:
            summary = sheets.get_summary('PJ')
            if summary:
                response = f"""
📊 *Resumo PJ - {sheets.get_sheet_by_month()}*

💰 Receita: R$ {summary['receita']:.2f}
💸 Despesa: R$ {summary['despesa']:.2f}
💼 Investimento: R$ {summary['investimento']:.2f}
📈 Saldo: R$ {summary['saldo']:.2f}
"""
        elif 'PF' in message_upper:
            summary = sheets.get_summary('PF')
            if summary:
                response = f"""
📊 *Resumo PF - {sheets.get_sheet_by_month()}*

💰 Receita: R$ {summary['receita']:.2f}
💸 Despesa: R$ {summary['despesa']:.2f}
💼 Investimento: R$ {summary['investimento']:.2f}
📈 Saldo: R$ {summary['saldo']:.2f}
"""
        else:
            response = "Use: 'Resumo PJ' ou 'Resumo PF'"

    else:
        # Tentar processar como transação
        tipo_pj_pf, tipo_movimento, categoria, valor, descricao, erro = whatsapp.parse_message(message_text)

        if erro:
            response = erro
        else:
            # Salvar na planilha
            sucesso, mensagem = sheets.add_transaction(
                tipo_pj_pf, tipo_movimento, categoria, descricao, valor
            )

            if sucesso:
                response = f"""
✅ *Transação Registrada!*

Tipo: {tipo_pj_pf}
Movimento: {tipo_movimento}
Categoria: {categoria}
Valor: R$ {valor:.2f}
Descrição: {descricao}

Confira em: https://docs.google.com/spreadsheets/d/1DblcpVwhnErzxtlh3ZEQJ7qpjTZMJO0Di_ygFTZFifI
"""
            else:
                response = mensagem

    # Enviar resposta
    if response:
        whatsapp.send_message(phone_number, response)

@app.route('/health', methods=['GET'])
def health():
    """Verifica se o servidor está rodando"""
    return jsonify({
        'status': 'running',
        'sheets_connected': sheets.service is not None,
        'whatsapp_configured': bool(whatsapp.access_token)
    })

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
