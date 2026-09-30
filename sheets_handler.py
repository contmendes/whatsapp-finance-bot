import json
import os
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient import discovery

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsHandler:
    def __init__(self):
        self.service = None
        self.credentials = None
        self.spreadsheet_id = os.getenv("SPREADSHEET_ID", "")
        self._authenticate()

    def _authenticate(self):
        try:
            credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
            if credentials_json:
                self.credentials = Credentials.from_service_account_info(json.loads(credentials_json), scopes=SCOPES)
            elif os.path.exists("credentials.json"):
                self.credentials = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
            else:
                print("[Google Sheets] GOOGLE_CREDENTIALS_JSON não configurado")
                return False
            self.service = discovery.build("sheets", "v4", credentials=self.credentials, cache_discovery=False)
            return True
        except Exception as error:
            print(f"[Google Sheets] erro de autenticação: {error}")
            return False

    def get_sheet_by_month(self):
        configured_name = os.getenv("SHEET_NAME", "").strip()
        if configured_name:
            return configured_name
        return ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"][datetime.now().month - 1]

    def add_transaction(self, tipo_pj_pf, tipo_movimento, categoria, descricao, valor, phone_number="", contact_name="", message_id="", raw_message=""):
        if not self.service or not self.spreadsheet_id:
            return False, "❌ Google Sheets não está configurado no Render."
        try:
            row = [
                datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                tipo_pj_pf,
                tipo_movimento,
                categoria,
                descricao,
                float(valor),
                "",
                phone_number,
                contact_name,
                message_id,
                raw_message,
            ]
            range_name = f"'{self.get_sheet_by_month()}'!A:K"
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()
            return True, f"✅ Transação registrada na aba '{self.get_sheet_by_month()}'."
        except Exception as error:
            print(f"[Google Sheets] erro ao adicionar transação: {error}")
            return False, "❌ Não consegui salvar na planilha. Verifique o acesso da conta de serviço."

    def get_summary(self, tipo_pj_pf):
        if not self.service or not self.spreadsheet_id:
            return None
        try:
            values = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f"'{self.get_sheet_by_month()}'!A:K",
            ).execute().get("values", [])
            summary = {"receita": 0.0, "despesa": 0.0, "investimento": 0.0, "saldo": 0.0}
            for row in values[1:]:
                if len(row) < 6 or row[1] != tipo_pj_pf:
                    continue
                try:
                    value = float(str(row[5]).replace(".", "").replace(",", "."))
                except ValueError:
                    continue
                movement = row[2].upper()
                if movement == "RECEITA": summary["receita"] += value
                elif movement == "DESPESA": summary["despesa"] += value
                elif movement == "INVESTIMENTO": summary["investimento"] += value
            summary["saldo"] = summary["receita"] - summary["despesa"]
            return summary
        except Exception as error:
            print(f"[Google Sheets] erro ao obter resumo: {error}")
            return None

    def format_summary(self, summary, tipo):
        return f"📊 *Resumo {tipo} - {self.get_sheet_by_month()}*\n\n💰 Receita: R$ {summary['receita']:.2f}\n💸 Despesa: R$ {summary['despesa']:.2f}\n💼 Investimento: R$ {summary['investimento']:.2f}\n📈 Saldo: R$ {summary['saldo']:.2f}"
