import os
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from google.api_python_client import discovery
from datetime import datetime

# ID da planilha - você deve colocar o seu aqui
SPREADSHEET_ID = "1DblcpVwhnErzxtlh3ZEQJ7qpjTZMJO0Di_ygFTZFifI"

# Escopos necessários
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

class SheetsHandler:
    def __init__(self):
        """Inicializa a conexão com Google Sheets"""
        self.service = None
        self.credentials = None
        self._authenticate()

    def _authenticate(self):
        """Autentica com Google Sheets usando variável de ambiente"""
        try:
            # Pegando as credenciais da variável de ambiente
            credentials_json = os.getenv('GOOGLE_CREDENTIALS_JSON')

            if not credentials_json:
                # Se não tiver variável de ambiente, tenta usar arquivo local
                if os.path.exists('credentials.json'):
                    self.credentials = Credentials.from_service_account_file(
                        'credentials.json', scopes=SCOPES
                    )
                else:
                    print("❌ AVISO: Credenciais do Google não encontradas!")
                    return False
            else:
                import json
                import io
                credentials_dict = json.loads(credentials_json)
                self.credentials = Credentials.from_service_account_info(
                    credentials_dict, scopes=SCOPES
                )

            self.service = discovery.build('sheets', 'v4', credentials=self.credentials)
            return True
        except Exception as e:
            print(f"❌ Erro ao autenticar: {e}")
            return False

    def get_sheet_by_month(self):
        """Retorna o nome da aba baseado no mês atual"""
        now = datetime.now()
        month_names = [
            "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
        ]
        return month_names[now.month - 1]

    def add_transaction(self, tipo_pj_pf, tipo_movimento, categoria, descricao, valor):
        """
        Adiciona uma transação à planilha

        Args:
            tipo_pj_pf: "PJ" ou "PF"
            tipo_movimento: "RECEITA", "DESPESA" ou "INVESTIMENTO"
            categoria: Nome da categoria
            descricao: Descrição da transação
            valor: Valor da transação (número)
        """
        try:
            sheet_name = self.get_sheet_by_month()

            # Preparar os dados da linha
            data = [
                datetime.now().strftime("%d/%m/%Y"),  # Data
                tipo_pj_pf,                            # Tipo
                tipo_movimento,                        # Movimento
                categoria,                             # Categoria
                descricao,                             # Descrição
                float(valor),                          # Valor
                "",                                    # Saldo (pode calcular depois)
                ""                                     # Observações
            ]

            # Anexar a linha
            range_name = f"'{sheet_name}'!A:H"
            body = {'values': [data]}

            result = self.service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name,
                valueInputOption='USER_ENTERED',
                body=body
            ).execute()

            return True, f"✅ Registrado na aba '{sheet_name}'"

        except Exception as e:
            print(f"❌ Erro ao adicionar transação: {e}")
            return False, f"❌ Erro ao salvar: {str(e)}"

    def get_summary(self, tipo_pj_pf):
        """Retorna um resumo das transações do tipo PJ ou PF"""
        try:
            sheet_name = self.get_sheet_by_month()
            range_name = f"'{sheet_name}'!A:H"

            result = self.service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=range_name
            ).execute()

            values = result.get('values', [])

            if not values:
                return None

            receita_total = 0
            despesa_total = 0
            investimento_total = 0

            # Iterar pelas linhas (pulando o cabeçalho se existir)
            for row in values[1:]:
                if len(row) >= 6:
                    tipo = row[1] if len(row) > 1 else ""
                    movimento = row[2] if len(row) > 2 else ""
                    valor = row[5] if len(row) > 5 else "0"

                    if tipo == tipo_pj_pf:
                        try:
                            val = float(valor)
                            if movimento == "RECEITA":
                                receita_total += val
                            elif movimento == "DESPESA":
                                despesa_total += val
                            elif movimento == "INVESTIMENTO":
                                investimento_total += val
                        except:
                            pass

            return {
                "receita": receita_total,
                "despesa": despesa_total,
                "investimento": investimento_total,
                "saldo": receita_total - despesa_total
            }

        except Exception as e:
            print(f"❌ Erro ao obter resumo: {e}")
            return None
