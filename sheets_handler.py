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

    @staticmethod
    def _parse_value(value):
        try:
            text = str(value).strip().replace("R$", "").replace(" ", "")
            if "," in text:
                text = text.replace(".", "").replace(",", ".")
            return float(text)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _parse_date(value):
        text = str(value or "").strip()
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
        return None

    def get_dashboard_data(self, month="", tipo="TODOS"):
        """Consolida as abas mensais; não inclui Categorias nem Perfis."""
        empty = {"months": [], "selected_month": month or "todos", "tipo": tipo, "metrics": {"receita": 0, "despesa": 0, "investimento": 0, "saldo": 0, "count": 0}, "by_category": [], "by_type": [], "recent": []}
        if not self.service or not self.spreadsheet_id:
            return empty
        try:
            metadata = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties(title)").execute()
            titles = [s.get("properties", {}).get("title", "") for s in metadata.get("sheets", [])]
            excluded = {"categorias", "perfis"}
            month_titles = [title for title in titles if title and title.casefold() not in excluded]
            rows = []
            for title in month_titles:
                values = self.service.spreadsheets().values().get(spreadsheetId=self.spreadsheet_id, range=f"'{title}'!A:K").execute().get("values", [])
                for row in values[1:]:
                    if len(row) < 6:
                        continue
                    row_date = self._parse_date(row[0])
                    row_type = str(row[1]).strip().upper()
                    if row_type not in ("PF", "PJ"):
                        continue
                    if tipo in ("PF", "PJ") and row_type != tipo:
                        continue
                    if month and month != "todos":
                        selected_sheet = title.casefold() == month.casefold()
                        selected_period = bool(row_date and row_date.strftime("%Y-%m") == month)
                        if not selected_sheet and not selected_period:
                            continue
                    rows.append({"date": row[0], "parsed_date": row_date, "type": row_type, "movement": str(row[2]).strip().upper(), "category": row[3] if len(row) > 3 else "Sem categoria", "description": row[4] if len(row) > 4 else "", "value": self._parse_value(row[5]), "sheet": title})
            metrics = {"receita": 0.0, "despesa": 0.0, "investimento": 0.0, "saldo": 0.0, "count": len(rows)}
            categories = {}
            types = {"PF": 0.0, "PJ": 0.0}
            for item in rows:
                movement = item["movement"].lower()
                if movement in metrics:
                    metrics[movement] += item["value"]
                if movement == "receita":
                    metrics["saldo"] += item["value"]
                elif movement == "despesa":
                    metrics["saldo"] -= item["value"]
                categories[item["category"]] = categories.get(item["category"], 0.0) + item["value"]
                types[item["type"]] += item["value"]
            rows.sort(key=lambda item: item["parsed_date"] or datetime.min, reverse=True)
            return {"months": month_titles, "selected_month": month or "todos", "tipo": tipo, "metrics": metrics, "by_category": [{"name": name, "value": value} for name, value in sorted(categories.items(), key=lambda pair: pair[1], reverse=True)[:10]], "by_type": [{"name": name, "value": value} for name, value in types.items()], "recent": [{key: item[key] for key in ("date", "type", "movement", "category", "description", "value", "sheet")} for item in rows[:12]]}
        except Exception as error:
            print(f"[Google Sheets] erro no dashboard: {error}")
            return empty

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

    def get_profile(self, phone_number, contact_name=""):
        """Obtém preferências do usuário pela aba Perfis, criando a aba quando necessário."""
        defaults = {"phone": phone_number, "name": contact_name or "Usuário", "default_type": "PF", "context": "PF", "company": ""}
        if not self.service or not self.spreadsheet_id:
            return defaults
        try:
            try:
                values = self.service.spreadsheets().values().get(
                    spreadsheetId=self.spreadsheet_id, range="'Perfis'!A:E"
                ).execute().get("values", [])
            except Exception:
                try:
                    self.service.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": [{"addSheet": {"properties": {"title": "Perfis"}}}]}).execute()
                except Exception as create_error:
                    if "already exists" not in str(create_error):
                        raise
                values = []
            for row in values[1:]:
                if row and row[0] == phone_number:
                    return {"phone": row[0], "name": row[1] if len(row) > 1 and row[1] else contact_name or "Usuário", "default_type": row[2] if len(row) > 2 and row[2] in ("PF", "PJ") else "PF", "context": row[3] if len(row) > 3 and row[3] in ("PF", "PJ") else "PF", "company": row[4] if len(row) > 4 else ""}
            if not values:
                self.service.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": [{"addSheet": {"properties": {"title": "Perfis"}}}]}).execute()
                self.service.spreadsheets().values().update(spreadsheetId=self.spreadsheet_id, range="'Perfis'!A1:E1", valueInputOption="USER_ENTERED", body={"values": [["Telefone", "Nome", "Tipo padrão", "Contexto atual", "Empresa"]]}).execute()
            self.service.spreadsheets().values().append(spreadsheetId=self.spreadsheet_id, range="'Perfis'!A:E", valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"values": [[phone_number, defaults["name"], "PF", "PF", ""]]}).execute()
            return defaults
        except Exception as error:
            print(f"[Google Sheets] erro ao obter perfil: {error}")
            return defaults

    def set_profile_context(self, phone_number, context, contact_name=""):
        profile = self.get_profile(phone_number, contact_name)
        profile["context"] = context
        if not self.service or not self.spreadsheet_id:
            return profile
        try:
            values = self.service.spreadsheets().values().get(spreadsheetId=self.spreadsheet_id, range="'Perfis'!A:E").execute().get("values", [])
            for idx, row in enumerate(values[1:], start=2):
                if row and row[0] == phone_number:
                    self.service.spreadsheets().values().update(spreadsheetId=self.spreadsheet_id, range=f"'Perfis'!B{idx}:E{idx}", valueInputOption="USER_ENTERED", body={"values": [[profile["name"], profile["default_type"], context, profile["company"]]]}).execute()
                    break
        except Exception as error:
            print(f"[Google Sheets] erro ao atualizar perfil: {error}")
        return profile

    def find_transactions(self, tipo=None, keyword=None, phone_number=None, limit=10):
        if not self.service or not self.spreadsheet_id:
            return []
        try:
            values = self.service.spreadsheets().values().get(spreadsheetId=self.spreadsheet_id, range=f"'{self.get_sheet_by_month()}'!A:K").execute().get("values", [])
            found = []
            for idx, row in enumerate(values[1:], start=2):
                if len(row) < 6 or (tipo and row[1] != tipo) or (phone_number and len(row) > 7 and row[7] != phone_number):
                    continue
                haystack = " ".join(str(x) for x in row).casefold()
                if keyword and keyword.casefold() not in haystack:
                    continue
                found.append({"row": idx, "date": row[0], "type": row[1], "movement": row[2], "category": row[3], "description": row[4], "value": row[5]})
                if len(found) >= limit:
                    break
            return found
        except Exception as error:
            print(f"[Google Sheets] erro ao consultar transações: {error}")
            return []

    def delete_transaction(self, row_number):
        if not self.service or not self.spreadsheet_id:
            return False
        try:
            self.service.spreadsheets().values().clear(spreadsheetId=self.spreadsheet_id, range=f"'{self.get_sheet_by_month()}'!A{row_number}:K{row_number}").execute()
            return True
        except Exception as error:
            print(f"[Google Sheets] erro ao apagar transação: {error}")
            return False

    def update_transaction(self, row_number, value=None, description=None, category=None):
        if not self.service or not self.spreadsheet_id:
            return False
        try:
            row = self.service.spreadsheets().values().get(spreadsheetId=self.spreadsheet_id, range=f"'{self.get_sheet_by_month()}'!A{row_number}:K{row_number}").execute().get("values", [[]])[0]
            while len(row) < 11:
                row.append("")
            if value is not None:
                row[5] = float(value)
            if category:
                row[3] = category
            if description:
                row[4] = description
            self.service.spreadsheets().values().update(spreadsheetId=self.spreadsheet_id, range=f"'{self.get_sheet_by_month()}'!A{row_number}:K{row_number}", valueInputOption="USER_ENTERED", body={"values": [row]}).execute()
            return True
        except Exception as error:
            print(f"[Google Sheets] erro ao atualizar transação: {error}")
            return False
