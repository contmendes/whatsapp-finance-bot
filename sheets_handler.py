import hashlib
import json
import os
import re
import secrets
import unicodedata
from datetime import datetime
from threading import Lock
from time import monotonic

from google.oauth2.service_account import Credentials
from googleapiclient import discovery
from categories import CATEGORIES

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

NEW_SPREADSHEET_ID = "1zGh1L0cYQ6nRN-ZkEfXzQvgTxSHZhWSu07ss3I5tHjQ"
MONTHS_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
STATUS_OPTIONS = ("Recebido", "A receber", "Pago", "A pagar")
HEADER_ALIASES = {
    "date": ("data",), "month": ("mês", "mes"), "year": ("ano",),
    "scope": ("escopo",), "category": ("categoria",), "code": ("código", "codigo"),
    "movement_type": ("tipo",), "description": ("descrição", "descricao"),
    "value": ("valor",), "account": ("conta",),
    "payment": ("forma pgto", "forma de pagamento"), "status": ("status",),
    "reconciled": ("conciliado",), "goal": ("meta",), "balance": ("saldo",),
    "user": ("usuário", "usuario"), "id": ("id", "id da mensagem"),
    "raw_message": ("mensagem original", "mensagem", "obs"),
}


class SheetsHandler:
    def __init__(self):
        self.service = None
        self.credentials = None
        # Compatibilidade com a planilha usada pelas versões anteriores do bot.
        self.spreadsheet_id = os.getenv("SPREADSHEET_ID", NEW_SPREADSHEET_ID)
        self._dashboard_cache = {}
        self._write_lock = Lock()
        self._processed_message_ids = set()
        self._config_cache = None
        self._config_cache_time = 0
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

    def _migrate_monthly_sheets(self):
        """Converte o layout antigo para o layout de gestão de caixa solicitado."""
        try:
            metadata = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties.title").execute()
            excluded = {"categorias", "perfis"}
            titles = [s.get("properties", {}).get("title", "") for s in metadata.get("sheets", [])]
            target_headers = ["Data", "Escopo", "Categoria", "Código", "Tipo", "Descrição", "Valor", "Conta", "Forma Pgto", "Status", "Conciliado", "Obs", "Saldo"]
            for title in titles:
                if not title or title.casefold() in excluded:
                    continue
                values = self.service.spreadsheets().values().get(spreadsheetId=self.spreadsheet_id, range=f"'{title}'!A:M").execute().get("values", [])
                if not values:
                    self.service.spreadsheets().values().update(spreadsheetId=self.spreadsheet_id, range=f"'{title}'!A1:M1", valueInputOption="USER_ENTERED", body={"values": [target_headers]}).execute()
                    continue
                header = [str(v).strip().casefold() for v in values[0]]
                custom_header_rows = [[str(v).strip().casefold() for v in row] for row in values[:4]]
                if any("saldo total" in row for row in custom_header_rows):
                    continue
                if "escopo" in header and "forma pgto" in header and "saldo" in header:
                    if len(values) > 1:
                        formulas = [[f'=IF(E{row_number}="E",G{row_number},-G{row_number})' if row_number == 2 else f'=M{row_number - 1}+IF(E{row_number}="E",G{row_number},-G{row_number})'] for row_number in range(2, len(values) + 1)]
                        self.service.spreadsheets().values().update(spreadsheetId=self.spreadsheet_id, range=f"'{title}'!M2:M{len(values)}", valueInputOption="USER_ENTERED", body={"values": formulas}).execute()
                    continue
                migrated = [target_headers]
                for old in values[1:]:
                    if not any(str(v).strip() for v in old):
                        continue
                    movement = str(old[2] if len(old) > 2 else "DESPESA").strip().upper()
                    is_income = movement == "RECEITA" or movement in ("E", "ENTRADA")
                    value = self._parse_value(old[5] if len(old) > 5 else 0)
                    bank = old[11] if len(old) > 11 else ""
                    payment = old[12] if len(old) > 12 else ""
                    old_phone = old[7] if len(old) > 7 else ""
                    old_name = old[8] if len(old) > 8 else ""
                    old_id = old[9] if len(old) > 9 else ""
                    raw = old[10] if len(old) > 10 else ""
                    migrated.append([
                        old[0] if len(old) > 0 else "",
                        old[1] if len(old) > 1 else "PF",
                        old[3] if len(old) > 3 else "Outro",
                        "",
                        "E" if is_income else "S",
                        old[4] if len(old) > 4 else "",
                        value,
                        bank,
                        payment,
                        "Recebido" if is_income else "Pago",
                        False,
                        raw,
                        "",
                    ])
                for row_number in range(2, len(migrated) + 1):
                    migrated[row_number - 1][12] = f'=IF(E{row_number}="E",G{row_number},-G{row_number})' if row_number == 2 else f'=M{row_number - 1}+IF(E{row_number}="E",G{row_number},-G{row_number})'
                self.service.spreadsheets().values().clear(spreadsheetId=self.spreadsheet_id, range=f"'{title}'!A:M").execute()
                self.service.spreadsheets().values().update(spreadsheetId=self.spreadsheet_id, range=f"'{title}'!A1:M{len(migrated)}", valueInputOption="USER_ENTERED", body={"values": migrated}).execute()
        except Exception as error:
            print(f"[Google Sheets] migração de layout não concluída: {error}")

    def _sync_cashflow_tabs_from_august(self):
        """Usa Agosto como modelo visual e aplica o saldo mensal encadeado nas abas."""
        months = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
        try:
            metadata = self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties(sheetId,title)"
            ).execute()
            sheet_map = {s["properties"]["title"]: s["properties"]["sheetId"] for s in metadata.get("sheets", []) if "properties" in s}
            if "Agosto" not in sheet_map:
                return

            format_requests = []
            for month in months:
                if month not in sheet_map:
                    continue
                if month != "Agosto":
                    format_requests.append({"copyPaste": {
                        "source": {"sheetId": sheet_map["Agosto"], "startRowIndex": 0, "endRowIndex": 1000, "startColumnIndex": 0, "endColumnIndex": 16},
                        "destination": {"sheetId": sheet_map[month], "startRowIndex": 0, "endRowIndex": 1000, "startColumnIndex": 0, "endColumnIndex": 16},
                        "pasteType": "PASTE_FORMAT",
                    }})
                    format_requests.append({"copyPaste": {
                        "source": {"sheetId": sheet_map["Agosto"], "startRowIndex": 0, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 16},
                        "destination": {"sheetId": sheet_map[month], "startRowIndex": 0, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 16},
                        "pasteType": "PASTE_NORMAL",
                    }})
                    format_requests.append({"copyPaste": {
                        "source": {"sheetId": sheet_map["Agosto"], "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 0, "endColumnIndex": 16},
                        "destination": {"sheetId": sheet_map[month], "startRowIndex": 2, "endRowIndex": 1000, "startColumnIndex": 0, "endColumnIndex": 16},
                        "pasteType": "PASTE_DATA_VALIDATION",
                    }})
            if format_requests:
                self.service.spreadsheets().batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": format_requests}).execute()

            for index, month in enumerate(months):
                if month not in sheet_map:
                    continue
                if month != "Agosto":
                    self.service.spreadsheets().values().clear(
                        spreadsheetId=self.spreadsheet_id, range=f"'{month}'!A3:P1000"
                    ).execute()
        except Exception as error:
            print(f"[Google Sheets] sincronização de abas não concluída: {error}")

    def sync_cashflow_tabs_from_august(self):
        """Executa explicitamente a sincronização pesada, fora do boot do servidor."""
        return self._sync_cashflow_tabs_from_august()

    def get_sheet_by_month(self):
        configured_name = os.getenv("SHEET_NAME", "").strip()
        return configured_name or "Lançamentos"

    def _sheet_for_date(self, transaction_date=None):
        configured_name = os.getenv("SHEET_NAME", "").strip()
        return configured_name or "Lançamentos"

    @staticmethod
    def _normal_header(value):
        return " ".join(str(value or "").strip().casefold().split())

    def _header_map(self, header_row):
        normalized = [self._normal_header(value) for value in header_row]
        result = {}
        for field, aliases in HEADER_ALIASES.items():
            for alias in aliases:
                if alias in normalized:
                    result[field] = normalized.index(alias)
                    break
        return result

    def _read_sheet(self, sheet_name=None):
        name = sheet_name or self.get_sheet_by_month()
        values = self.service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id, range=f"'{name}'!A:Z"
        ).execute().get("values", [])
        header_idx = next((i for i, row in enumerate(values[:10])
                           if "data" in [self._normal_header(x) for x in row]
                           and ("escopo" in [self._normal_header(x) for x in row] or "tipo" in [self._normal_header(x) for x in row])
                           and "valor" in [self._normal_header(x) for x in row]), None)
        if header_idx is None:
            raise ValueError(f"Não encontrei os cabeçalhos esperados na aba '{name}'.")
        return values, header_idx, self._header_map(values[header_idx])

    @staticmethod
    def _get(row, columns, field, default=""):
        index = columns.get(field)
        return row[index] if index is not None and index < len(row) else default

    @staticmethod
    def _column_letter(index):
        label = ""
        index += 1
        while index:
            index, rem = divmod(index - 1, 26)
            label = chr(65 + rem) + label
        return label

    @staticmethod
    def _normalize_option(value):
        text = unicodedata.normalize("NFD", str(value or "").casefold())
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")
        return re.sub(r"[^a-z0-9]+", " ", text).strip()

    @classmethod
    def match_option(cls, value, options):
        key = cls._normalize_option(value)
        if not key:
            return None
        return next((option for option in options if cls._normalize_option(option) == key), None)

    @staticmethod
    def _short_numeric_id(message_id=""):
        if message_id:
            digest = hashlib.sha256(str(message_id).encode("utf-8")).digest()
            return 100_000_000_000 + int.from_bytes(digest[:8], "big") % 900_000_000_000
        return secrets.randbelow(900_000_000_000) + 100_000_000_000

    def _next_transaction_row(self, values, header_idx, columns):
        """Choose the row after the last real transaction, ignoring formulas and metadata."""
        occupied_fields = ("date", "scope", "category", "description", "value",
                           "account", "payment", "status", "user", "id")
        last_data_row = header_idx + 1
        for sheet_row, existing in enumerate(values[header_idx + 1:], start=header_idx + 2):
            if any(str(self._get(existing, columns, field)).strip() for field in occupied_fields):
                last_data_row = sheet_row
        return last_data_row + 1

    def _config_rows(self):
        if self._config_cache is not None and monotonic() - self._config_cache_time < 60:
            return self._config_cache
        try:
            rows = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id, range="'Config'!A:U"
            ).execute().get("values", [])
            self._config_cache = rows
            self._config_cache_time = monotonic()
            return rows
        except Exception:
            return []

    def _category_code(self, category):
        target = self._normal_header(category)
        for row in self._config_rows()[2:]:
            if len(row) > 1 and self._normal_header(row[1]) == target and row[0].strip():
                return row[0].strip()
        return ""

    def _resolve_category(self, category, scope, movement):
        """Accept only a category that exists in the data-validation list for column E."""
        options = self.get_category_options()
        return self.match_option(category, options) if options else (category or "Outro")

    def _config_options(self, column_index):
        return [str(row[column_index]).strip() for row in self._config_rows()[2:]
                if len(row) > column_index and str(row[column_index]).strip()]

    def _add_config_option(self, column_index, value):
        if not self.service or not self.spreadsheet_id:
            return False, "", False, "❌ Google Sheets não está configurado."
        if column_index not in (4, 6):
            return False, "", False, "❌ Não é permitido alterar essa lista da planilha."
        name = " ".join(str(value or "").split())
        if len(name) < 2 or len(name) > 80 or any(ord(char) < 32 for char in name):
            return False, "", False, "❌ O nome precisa ter entre 2 e 80 caracteres."
        try:
            with self._write_lock:
                self._config_cache = None
                self._config_cache_time = 0
                options = self._config_options(column_index)
                existing = self.match_option(name, options)
                if existing:
                    return True, existing, False, ""
                rows = self._config_rows()
                last_used_row = 2
                for row_number, row in enumerate(rows, start=1):
                    if row_number >= 3 and len(row) > column_index and str(row[column_index] or "").strip():
                        last_used_row = row_number
                target_row = last_used_row + 1
                if target_row > 1000:
                    return False, "", False, "❌ A lista da planilha está cheia; não consegui adicionar essa opção."
                column = self._column_letter(column_index)
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'Config'!{column}{target_row}",
                    valueInputOption="RAW",
                    body={"values": [[name]]},
                ).execute()
                self._config_cache = None
                self._config_cache_time = 0
            return True, name, True, ""
        except Exception as error:
            label = "forma de pagamento" if column_index == 4 else "conta"
            print(f"[Google Sheets] erro ao adicionar opção de {label}: {error}")
            return False, "", False, f"❌ Não consegui adicionar essa opção à planilha. Verifique se ela permite edição e tente novamente."

    def add_account_option(self, account):
        """Add an account label only to Config!G, the source of the Conta validation."""
        return self._add_config_option(6, account)

    def add_payment_method_option(self, payment_method):
        """Add a payment label only to Config!E, the source of the Forma Pgto validation."""
        return self._add_config_option(4, payment_method)

    def get_category_options(self):
        options = self._config_options(16)  # Config!Q, validação da coluna E.
        if options:
            return list(dict.fromkeys(options))
        categories = self.get_categories()
        return list(dict.fromkeys(
            name for scope in categories.values() for group in scope.values() for name in group
        ))

    @staticmethod
    def _category_code_prefix(scope, movement):
        scope = str(scope or "PF").strip().upper()
        movement = str(movement or "DESPESA").strip().upper()
        if movement in ("INVESTIMENTO", "INVESTIMENTOS"):
            return "7", 2, 98
        if scope == "PF":
            if movement in ("RECEITA", "ENTRADA", "E"):
                return "9.01", 8, 98
            return "9.12", 2, 98
        if movement in ("RECEITA", "ENTRADA", "E"):
            return "1", 3, 98
        return "4.99", 1, 98

    @staticmethod
    def _is_transaction_category(code, name):
        code, name = str(code or "").strip(), str(name or "").strip()
        return bool(code and len(name) >= 4 and (not code.startswith("9.") or len(code) >= 7))

    def add_category_to_plan(self, category, scope, movement):
        """Add a user-supplied category to Config's plan of accounts.

        The sheet derives Config!Q/S/U and the Lançamentos dropdown from columns A:B,
        so writing the account code and name is the source-of-truth update.
        """
        if not self.service or not self.spreadsheet_id:
            return False, "", False, "❌ Google Sheets não está configurado."
        name = " ".join(str(category or "").split())
        if len(name) < 4 or len(name) > 60 or any(ord(char) < 32 for char in name):
            return False, "", False, "❌ O nome da categoria precisa ter entre 4 e 60 caracteres para aparecer no menu da planilha."
        if str(scope or "").upper() not in ("PF", "PJ", "GERAL"):
            return False, "", False, "❌ Não consegui identificar se a categoria é PF ou PJ."
        if str(movement or "").upper() not in ("RECEITA", "ENTRADA", "E", "DESPESA", "INVESTIMENTO", "INVESTIMENTOS"):
            movement = "DESPESA"

        try:
            with self._write_lock:
                values = self.service.spreadsheets().values().get(
                    spreadsheetId=self.spreadsheet_id,
                    range="'Config'!A3:B1004",
                    valueRenderOption="UNFORMATTED_VALUE",
                ).execute().get("values", [])
                target_key = self._normalize_option(name)
                existing_codes = set()
                last_used_row = 2
                for row_number, row in enumerate(values, start=3):
                    code = str(row[0]).strip() if len(row) > 0 and row[0] is not None else ""
                    existing_name = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
                    if code or existing_name:
                        last_used_row = row_number
                    if code:
                        existing_codes.add(code)
                    if code and existing_name and self._normalize_option(existing_name) == target_key:
                        if self._is_transaction_category(code, existing_name):
                            return True, existing_name, False, ""
                        return False, "", False, "❌ Esse nome já é um grupo do plano de contas, não uma categoria lançável. Escolha um nome diferente."

                target_row = last_used_row + 1
                if target_row > 1002:
                    return False, "", False, "❌ O plano de contas está cheio; não consegui adicionar essa categoria."

                prefix, start, end = self._category_code_prefix(scope, movement)
                code = ""
                for number in range(start, end + 1):
                    candidate = f"{prefix}.{number:02d}"
                    if candidate not in existing_codes:
                        code = candidate
                        break
                if not code:
                    return False, "", False, "❌ Não encontrei um código livre para essa categoria no plano de contas."

                self.service.spreadsheets().values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"'Config'!A{target_row}:B{target_row}",
                    valueInputOption="RAW",
                    body={"values": [[code, name]]},
                ).execute()
                self._config_cache = None
                self._config_cache_time = 0
                self._dashboard_cache.clear()
            return True, name, True, ""
        except Exception as error:
            print(f"[Google Sheets] erro ao criar categoria no plano de contas: {error}")
            return False, "", False, "❌ Não consegui adicionar a categoria ao plano de contas. A planilha pode estar sem espaço disponível ou sem permissão de edição."

    def get_meta_options(self):
        if not self.service or not self.spreadsheet_id:
            return []
        try:
            values = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range="'Metas e Limites'!G3:G42",
            ).execute().get("values", [])
            return list(dict.fromkeys(
                str(row[0]).strip() for row in values if row and str(row[0]).strip()
            ))
        except Exception as error:
            print(f"[Google Sheets] erro ao ler metas disponíveis: {error}")
            return []

    def get_transaction_options(self):
        return {
            "categories": self.get_category_options(),
            "accounts": self._config_options(6),  # Config!G, validação da coluna J.
            "payment_methods": self._config_options(4),  # Config!E, validação da coluna K.
            "goals": self.get_meta_options(),
            "statuses": list(STATUS_OPTIONS),
        }

    def add_transaction(self, tipo_pj_pf, tipo_movimento, categoria, descricao, valor, phone_number="", contact_name="", message_id="", raw_message="", transaction_date=None, bank="", payment_method="", account="", meta="", status="", category_added=False):
        if not self.service or not self.spreadsheet_id:
            return False, "❌ Google Sheets não está configurado no Render."
        try:
            with self._write_lock:
                if message_id and message_id in self._processed_message_ids:
                    return True, "✅ Esta mensagem já foi processada; não registrei novamente."
                sheet_name = self._sheet_for_date(transaction_date)
                values, header_idx, columns = self._read_sheet(sheet_name)
                required = ("date", "scope", "category", "movement_type", "description", "value", "status", "id")
                if any(field not in columns for field in required):
                    return False, "❌ Os cabeçalhos da aba Lançamentos não correspondem ao modelo esperado."
                numeric_id = self._short_numeric_id(message_id)
                if message_id and any(str(self._get(existing, columns, "id")).strip() in (str(message_id), str(numeric_id)) for existing in values[header_idx + 1:]):
                    self._processed_message_ids.add(message_id)
                    return True, "✅ Esta mensagem já foi processada; não registrei novamente."

                date_text = str(transaction_date or "")[:10]
                try:
                    date_value = datetime.strptime(date_text, "%Y-%m-%d") if date_text else datetime.now()
                except ValueError:
                    date_value = self._parse_date(transaction_date) or datetime.now()
                movement = str(tipo_movimento or "DESPESA").strip().upper()
                is_income = movement in ("RECEITA", "ENTRADA", "E")
                valid_statuses = ("Recebido", "A receber") if is_income else ("Pago", "A pagar")
                if status:
                    status = self.match_option(status, valid_statuses)
                    if not status:
                        return False, "❌ O Status precisa ser uma opção válida da planilha."
                else:
                    status = "Recebido" if is_income else "Pago"

                account_options = self._config_options(6)
                payment_options = self._config_options(4)
                account = self.match_option(account, account_options) if account_options else account
                if account_options and not account:
                    return False, "❌ Não consegui associar uma Conta válida do menu da planilha."
                if account and account_options and not self.match_option(account, account_options):
                    return False, "❌ A Conta informada não corresponde às opções da planilha."
                meta_options = self.get_meta_options()
                if meta:
                    meta = self.match_option(meta, meta_options) if meta_options else ""
                    if not meta:
                        return False, "❌ A Meta informada não corresponde às opções da planilha."
                payment = ""
                method_key = self._normal_header(payment_method)
                if method_key:
                    exact_payment = self.match_option(payment_method, payment_options)
                    payment_matches = [option for option in payment_options if method_key in self._normal_header(option)]
                    if account:
                        account_key = self._normal_header(account)
                        payment_matches = [option for option in payment_matches if account_key in self._normal_header(option)]
                    if exact_payment:
                        if account and self._normal_header(account) not in self._normal_header(exact_payment):
                            return False, "❌ A Forma de Pgto selecionada está vinculada a outra conta da planilha."
                        payment = exact_payment
                    elif len(payment_matches) == 1:
                        payment = payment_matches[0]
                    else:
                        return False, "❌ A Forma de Pgto não corresponde a uma opção única do menu da planilha."
                categoria = (str(categoria or "").strip() if category_added
                             else self._resolve_category(categoria, tipo_pj_pf, movement))
                if not categoria:
                    return False, "❌ A Categoria informada não corresponde às opções da planilha."

                # B/C/F/G/O são calculadas por fórmulas; M é checkbox. N é o campo Meta.
                # Fórmulas/checkbox não deslocam o append nem recebem valores do bot.
                next_row = self._next_transaction_row(values, header_idx, columns)
                user_value = f"{contact_name} ({phone_number})" if contact_name and phone_number else (contact_name or phone_number)
                row_values = {
                    "date": date_value.strftime("%d/%m/%Y"),
                    "scope": str(tipo_pj_pf).upper(),
                    "category": categoria,
                    "description": str(raw_message or descricao).strip(),
                    "value": float(valor),
                    "account": account,
                    "payment": payment,
                    "status": status,
                    "goal": meta,
                    "user": user_value,
                    "id": numeric_id,
                }
                writes = [{"range": f"'{sheet_name}'!{self._column_letter(columns[field])}{next_row}", "values": [[value]]}
                          for field, value in row_values.items() if field in columns]
                self.service.spreadsheets().values().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"valueInputOption": "USER_ENTERED", "data": writes},
                ).execute()
                if message_id:
                    self._processed_message_ids.add(message_id)
            self._dashboard_cache.clear()
            movement_label = "investimento" if movement == "INVESTIMENTO" else ("receita" if is_income else "despesa")
            return True, (f"✅ Anotado: {movement_label} de R$ {float(valor):.2f} em {date_value.strftime('%d/%m/%Y')}\n"
                          f"Categoria: {categoria} · Conta: {account} · Meta: {meta or 'sem meta'} · Status: {status}.")
        except Exception as error:
            print(f"[Google Sheets] erro ao adicionar transação: {error}")
            return False, "❌ Não consegui salvar na planilha. Verifique o acesso da conta de serviço."

    def get_summary(self, tipo_pj_pf):
        if not self.service or not self.spreadsheet_id:
            return None
        try:
            values, header_idx, columns = self._read_sheet()
            summary = {"receita": 0.0, "despesa": 0.0, "investimento": 0.0, "saldo": 0.0}
            for row in values[header_idx + 1:]:
                if not str(self._get(row, columns, "date")).strip() or str(self._get(row, columns, "scope")).strip().upper() != tipo_pj_pf:
                    continue
                value = self._parse_value(self._get(row, columns, "value"))
                movement = str(self._get(row, columns, "movement_type")).strip().upper()
                code = str(self._get(row, columns, "code")).strip()
                category = self._normal_header(self._get(row, columns, "category"))
                if code.startswith("7.") or "investimento" in category:
                    summary["investimento"] += value
                elif movement in ("E", "ENTRADA", "RECEITA"):
                    summary["receita"] += value
                elif movement in ("S", "SAÍDA", "SAIDA", "DESPESA"):
                    summary["despesa"] += value
            summary["saldo"] = summary["receita"] - summary["despesa"] - summary["investimento"]
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
        """Consolida os lançamentos da aba única, filtrando por período e escopo."""
        cache_key = (month or "todos", tipo or "TODOS")
        cached = self._dashboard_cache.get(cache_key)
        if cached and monotonic() - cached[0] < 30:
            return cached[1]
        empty = {"months": [self.get_sheet_by_month()], "selected_month": month or "todos", "tipo": tipo, "metrics": {"receita": 0, "despesa": 0, "investimento": 0, "saldo": 0, "count": 0}, "by_category": [], "by_type": [], "recent": []}
        if not self.service or not self.spreadsheet_id:
            return empty
        try:
            values, header_idx, columns = self._read_sheet()
            rows = []
            for row in values[header_idx + 1:]:
                date_text = str(self._get(row, columns, "date")).strip()
                if not date_text:
                    continue
                row_date = self._parse_date(date_text)
                row_type = str(self._get(row, columns, "scope")).strip().upper()
                if row_type not in ("PF", "PJ") or (tipo in ("PF", "PJ") and row_type != tipo):
                    continue
                if month and month.casefold() not in ("todos", "all"):
                    month_key = str(month).casefold()
                    month_value = self._normal_header(self._get(row, columns, "month"))
                    selected = month_value == month_key or bool(row_date and (row_date.strftime("%Y-%m") == month_key or str(row_date.month - 1) == month_key))
                    if not selected:
                        continue
                code = str(self._get(row, columns, "code")).strip()
                category = self._get(row, columns, "category", "Sem categoria") or "Sem categoria"
                mtype = str(self._get(row, columns, "movement_type")).strip().upper()
                movement = "INVESTIMENTO" if code.startswith("7.") else ("RECEITA" if mtype in ("E", "ENTRADA", "RECEITA") else "DESPESA")
                rows.append({"date": date_text, "parsed_date": row_date, "type": row_type, "movement": movement,
                             "category": category, "description": self._get(row, columns, "description"),
                             "value": self._parse_value(self._get(row, columns, "value")),
                             "bank": self._get(row, columns, "account"),
                             "payment_method": self._get(row, columns, "payment"), "sheet": self.get_sheet_by_month()})
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
                elif movement == "investimento":
                    metrics["saldo"] -= item["value"]
                categories[item["category"]] = categories.get(item["category"], 0.0) + item["value"]
                types[item["type"]] += item["value"]
            rows.sort(key=lambda item: item["parsed_date"] or datetime.min, reverse=True)
            result = {"months": [self.get_sheet_by_month()], "selected_month": month or "todos", "tipo": tipo, "metrics": metrics, "by_category": [{"name": name, "value": value} for name, value in sorted(categories.items(), key=lambda pair: pair[1], reverse=True)[:10]], "by_type": [{"name": name, "value": value} for name, value in types.items()], "recent": [{key: item[key] for key in ("date", "type", "movement", "category", "description", "value", "sheet")} for item in rows[:12]]}
            self._dashboard_cache[cache_key] = (monotonic(), result)
            return result
        except Exception as error:
            print(f"[Google Sheets] erro no dashboard: {error}")
            return empty

    def get_categories(self):
        """Lê as categorias PF/PJ liberadas na aba Config; mantém fallback legado."""
        if not self.service or not self.spreadsheet_id:
            return CATEGORIES
        try:
            config = self._config_rows()
            if len(config) > 2:
                result = {"PF": {"RECEITA": [], "DESPESA": []}, "PJ": {"RECEITA": [], "DESPESA": []}, "GERAL": {"INVESTIMENTO": []}}
                for scope, column in (("PJ", 18), ("PF", 20)):
                    names = []
                    for row in config[2:]:
                        if len(row) > column and row[column].strip() and row[column].strip() not in names:
                            names.append(row[column].strip())
                    for name in names:
                        code = self._category_code(name)
                        if code.startswith("7."):
                            result["GERAL"]["INVESTIMENTO"].append(name)
                        elif code.startswith(("1", "5.01", "6.01", "9.01")):
                            result[scope]["RECEITA"].append(name)
                        else:
                            result[scope]["DESPESA"].append(name)
                if any(result[scope][kind] for scope in ("PF", "PJ") for kind in ("RECEITA", "DESPESA")):
                    return result
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
            values, header_idx, columns = self._read_sheet()
            found = []
            for idx, row in enumerate(values[header_idx + 1:], start=header_idx + 2):
                if not str(self._get(row, columns, "date")).strip():
                    continue
                scope = str(self._get(row, columns, "scope")).strip().upper()
                if tipo and scope != tipo:
                    continue
                haystack = " ".join(str(x) for x in row).casefold()
                user = str(self._get(row, columns, "user"))
                if phone_number and phone_number not in user:
                    continue
                if keyword and keyword.casefold() not in haystack:
                    continue
                found.append({"row": idx, "date": self._get(row, columns, "date"), "type": scope,
                              "movement": self._get(row, columns, "movement_type"),
                              "category": self._get(row, columns, "category"),
                              "description": self._get(row, columns, "description"),
                              "value": self._get(row, columns, "value")})
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
            _, _, columns = self._read_sheet()
            fields = ("date", "month", "year", "scope", "category", "code", "movement_type", "description", "value", "account", "payment", "status", "goal", "user", "id")
            data = [{"range": f"'{self.get_sheet_by_month()}'!{self._column_letter(columns[field])}{row_number}", "values": [[""]]}
                    for field in fields if field in columns]
            self.service.spreadsheets().values().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": data},
            ).execute()
            self._dashboard_cache.clear()
            return True
        except Exception as error:
            print(f"[Google Sheets] erro ao apagar transação: {error}")
            return False

    def update_transaction(self, row_number, value=None, description=None, category=None):
        if not self.service or not self.spreadsheet_id:
            return False
        try:
            _, _, columns = self._read_sheet()
            writes = []
            for field, new_value in (("value", float(value) if value is not None else None),
                                     ("description", description), ("category", category)):
                if new_value is not None and field in columns:
                    writes.append({"range": f"'{self.get_sheet_by_month()}'!{self._column_letter(columns[field])}{row_number}", "values": [[new_value]]})
            if category and "code" in columns:
                writes.append({"range": f"'{self.get_sheet_by_month()}'!{self._column_letter(columns['code'])}{row_number}", "values": [[self._category_code(category)]]})
            if writes:
                self.service.spreadsheets().values().batchUpdate(spreadsheetId=self.spreadsheet_id,
                    body={"valueInputOption": "USER_ENTERED", "data": writes}).execute()
            self._dashboard_cache.clear()
            return True
        except Exception as error:
            print(f"[Google Sheets] erro ao atualizar transação: {error}")
            return False
