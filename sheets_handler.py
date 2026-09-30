import json
import os
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient import discovery
from categories import CATEGORIES

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsHandler:
    def __init__(self):
        self.service = None
        self.credentials = None
        # Compatibilidade com a planilha usada pelas versões anteriores do bot.
        self.spreadsheet_id = os.getenv("SPREADSHEET_ID", "1DblcpVwhnErzxtlh3ZEQJ7qpjTZMJO0Di_ygFTZFifI")
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

    def get_categories(self):
        """Lê a aba Categorias; usa o catálogo padrão se ela ainda não existir."""
        if not self.service or not self.spreadsheet_id:
            return CATEGORIES
        try:
            values = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id, range="'Categorias'!A:C"
            ).execute().get("values", [])
            if len(values) < 2:
                return CATEGORIES
            result = {"PF": {"RECEITA": [], "DESPESA": []}, "PJ": {"RECEITA": [], "DESPESA": []}, "GERAL": {"INVESTIMENTO": []}}
            for row in values[1:]:
                if len(row) < 3:
                    continue
                tipo, movimento, nome = row[0].strip().upper(), row[1].strip().upper(), row[2].strip()
                if tipo in result and movimento in result[tipo] and nome:
                    result[tipo][movimento].append(nome)
            return result if any(result[t].get(m) for t in result for m in result[t]) else CATEGORIES
        except Exception:
            return CATEGORIES

    def update_category(self, action, tipo, movimento, nome):
        """Adiciona/remove categoria na aba Categorias, criando o cabeçalho quando necessário."""
        if not self.service or not self.spreadsheet_id:
            return False, "❌ Google Sheets não está configurado."
        tipo, movimento, nome = tipo.upper(), movimento.upper(), nome.strip()
        if tipo not in ("PF", "PJ", "GERAL") or movimento not in ("RECEITA", "DESPESA", "INVESTIMENTO") or not nome:
            return False, "❌ Use: categoria adicionar PF despesa Mercado"
        try:
            try:
                values = self.service.spreadsheets().values().get(
                    spreadsheetId=self.spreadsheet_id, range="'Categorias'!A:C"
                ).execute().get("values", [])
            except Exception:
                self.service.spreadsheets().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"requests": [{"addSheet": {"properties": {"title": "Categorias"}}}]},
                ).execute()
                values = []
            if not values:
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id, range="'Categorias'!A1:C1",
                    valueInputOption="USER_ENTERED", body={"values": [["Tipo", "Movimento", "Categoria"]]},
                ).execute()
                values = [["Tipo", "Movimento", "Categoria"]]
            rows = {(r[0].strip().upper(), r[1].strip().upper(), r[2].strip().casefold()) for r in values[1:] if len(r) >= 3}
            key = (tipo, movimento, nome.casefold())
            if action == "adicionar":
                if key in rows:
                    return True, f"ℹ️ A categoria *{nome}* já existe em {tipo} {movimento.title()}."
                self.service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id, range="'Categorias'!A:C",
                    valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                    body={"values": [[tipo, movimento, nome]]},
                ).execute()
                return True, f"✅ Categoria *{nome}* adicionada em {tipo} {movimento.title()}."
            if action == "remover":
                for idx, row in enumerate(values[1:], start=2):
                    if len(row) >= 3 and (row[0].strip().upper(), row[1].strip().upper(), row[2].strip().casefold()) == key:
                        self.service.spreadsheets().values().clear(
                            spreadsheetId=self.spreadsheet_id, range=f"'Categorias'!A{idx}:C{idx}"
                        ).execute()
                        return True, f"✅ Categoria *{nome}* removida de {tipo} {movimento.title()}."
                return False, f"❌ Não encontrei a categoria *{nome}* em {tipo} {movimento.title()}."
            return False, "❌ Ação inválida. Use adicionar ou remover."
        except Exception as error:
            print(f"[Google Sheets] erro ao editar categoria: {error}")
            return False, "❌ Não consegui editar a aba Categorias."
