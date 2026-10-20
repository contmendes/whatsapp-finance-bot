import re
import unittest
from threading import Lock
from unittest.mock import patch

from sheets_handler import SheetsHandler


HEADERS = [
    "Data", "Mês", "Ano", "Escopo", "Categoria", "Código", "Tipo",
    "Descrição", "Valor", "Conta", "Forma Pgto", "Status", "Conciliado",
    "Meta", "Saldo", "Usuário", "ID",
]


class FakeRequest:
    def __init__(self, result=None):
        self.result = result or {}

    def execute(self):
        return self.result


class FakeValues:
    def __init__(self, rows):
        self.rows = rows
        self.last_batch = None
        self.last_update = None

    def get(self, **kwargs):
        return FakeRequest({"values": self.rows})

    def batchUpdate(self, **kwargs):
        self.last_batch = kwargs["body"]
        return FakeRequest({})

    def update(self, **kwargs):
        self.last_update = kwargs
        return FakeRequest({})


class FakeSpreadsheet:
    def __init__(self, rows):
        self.values_api = FakeValues(rows)

    def values(self):
        return self.values_api


class FakeService:
    def __init__(self, rows):
        self.spreadsheet = FakeSpreadsheet(rows)

    def spreadsheets(self):
        return self.spreadsheet


def blank_row():
    return [""] * len(HEADERS)


def set_cell(row, column, value):
    row[HEADERS.index(column)] = value
    return row


def existing_transaction(message_id):
    row = blank_row()
    for column, value in {
        "Data": "01/10/2026", "Escopo": "PF", "Categoria": "Mercado",
        "Descrição": "Compra", "Valor": 20, "Status": "Pago", "ID": message_id,
    }.items():
        set_cell(row, column, value)
    return row


def column_from_range(a1_range):
    match = re.search(r"!([A-Z]+)\d+$", a1_range)
    if not match:
        raise AssertionError(f"Unexpected A1 range: {a1_range}")
    return match.group(1)


class SheetWriteMappingTests(unittest.TestCase):
    def test_meta_checkbox_and_formula_columns_do_not_push_next_row(self):
        rows = [HEADERS.copy(), existing_transaction("old-message")]
        for _ in range(3, 51):
            row = blank_row()
            set_cell(row, "Conciliado", False)
            rows.append(row)
        row_51 = blank_row()
        set_cell(row_51, "Conciliado", False)
        set_cell(row_51, "Meta", "Meta existente")
        rows.append(row_51)

        handler = SheetsHandler.__new__(SheetsHandler)
        columns = handler._header_map(HEADERS)
        self.assertEqual(handler._next_transaction_row(rows, 0, columns), 3)

    def test_append_writes_only_input_columns_and_validates_user_choices(self):
        rows = [HEADERS.copy(), existing_transaction("old-message")]
        for _ in range(3, 51):
            row = blank_row()
            set_cell(row, "Conciliado", False)
            rows.append(row)
        row_51 = blank_row()
        set_cell(row_51, "Conciliado", False)
        set_cell(row_51, "Meta", "Meta existente")
        rows.append(row_51)
        rows.append(existing_transaction("message-at-52"))
        rows.append(existing_transaction("message-at-53"))

        handler = SheetsHandler.__new__(SheetsHandler)
        handler.service = FakeService(rows)
        handler.spreadsheet_id = "test-spreadsheet"
        handler._dashboard_cache = {}
        handler._write_lock = Lock()
        handler._processed_message_ids = set()
        handler._config_cache = None
        handler._config_cache_time = 0
        handler._config_options = lambda column_index: {
            4: ["Pix Conta PF", "Cartão de crédito Conta PF"],
            6: ["Conta PF", "Conta PJ"],
        }.get(column_index, [])
        handler.get_category_options = lambda: ["Mercado", "Transporte"]
        handler.get_meta_options = lambda: ["Meta existente", "Meta nova"]

        original_message = "Paguei R$ 42,50 no mercado com minha conta PF"
        with patch.dict("os.environ", {"SHEET_NAME": "Lançamentos"}):
            success, _ = handler.add_transaction(
                "PF", "DESPESA", "Mercado", "descrição curta", 42.50,
                phone_number="5511999999999", contact_name="Contato",
                message_id="new-message", raw_message=original_message,
                account="Conta PF", meta="Meta existente", status="Pago",
                payment_method="Pix",
            )

        self.assertTrue(success)
        body = handler.service.spreadsheet.values_api.last_batch
        self.assertIsNotNone(body)
        writes = body["data"]
        self.assertTrue(writes)
        self.assertTrue(all(entry["range"].endswith("54") for entry in writes))
        written_columns = {column_from_range(entry["range"]) for entry in writes}
        self.assertEqual(written_columns, {"A", "D", "E", "H", "I", "J", "K", "L", "N", "P", "Q"})
        self.assertTrue(written_columns.isdisjoint({"B", "C", "F", "G", "M", "O"}))
        cells = {column_from_range(entry["range"]): entry["values"][0][0] for entry in writes}
        self.assertEqual(cells["H"], original_message)
        self.assertEqual(cells["J"], "Conta PF")
        self.assertEqual(cells["L"], "Pago")
        self.assertEqual(cells["N"], "Meta existente")
        self.assertIsInstance(cells["Q"], int)
        self.assertRegex(str(cells["Q"]), r"^\d{12}$")

        with patch.dict("os.environ", {"SHEET_NAME": "Lançamentos"}):
            invalid_success, invalid_message = handler.add_transaction(
                "PF", "DESPESA", "Mercado", "Compra", 12,
                message_id="wrong-status", raw_message="gastei 12",
                account="Conta PF", meta="Meta existente", status="Recebido",
            )
        self.assertFalse(invalid_success)
        self.assertIn("Status", invalid_message)

    def test_numeric_message_id_is_short_and_repeatable(self):
        first = SheetsHandler._short_numeric_id("wamid.example-message")
        second = SheetsHandler._short_numeric_id("wamid.example-message")
        self.assertEqual(first, second)
        self.assertRegex(str(first), r"^\d{12}$")

    def test_option_matching_ignores_accents_and_case(self):
        self.assertEqual(SheetsHandler.match_option("cartao de credito", ["Cartão de crédito"]), "Cartão de crédito")

    def test_add_payment_method_appends_only_to_config_column_e(self):
        config_rows = [[""] * 21 for _ in range(4)]
        config_rows[2][4] = "Pix"
        config_rows[3][4] = "Cartão de crédito"
        handler = SheetsHandler.__new__(SheetsHandler)
        handler.service = FakeService(config_rows)
        handler.spreadsheet_id = "test-spreadsheet"
        handler._write_lock = Lock()
        handler._config_cache = None
        handler._config_cache_time = 0

        success, name, created, error = handler.add_payment_method_option("Apple Pay")

        self.assertTrue(success, error)
        self.assertEqual(name, "Apple Pay")
        self.assertTrue(created)
        self.assertEqual(handler.service.spreadsheet.values_api.last_update["range"], "'Config'!E5")
        self.assertEqual(handler.service.spreadsheet.values_api.last_update["body"]["values"], [["Apple Pay"]])

    def test_add_account_appends_only_to_config_column_g(self):
        config_rows = [[""] * 21 for _ in range(4)]
        config_rows[2][6] = "Itaú"
        config_rows[3][6] = "Inter Pessoal"
        handler = SheetsHandler.__new__(SheetsHandler)
        handler.service = FakeService(config_rows)
        handler.spreadsheet_id = "test-spreadsheet"
        handler._write_lock = Lock()
        handler._config_cache = None
        handler._config_cache_time = 0

        success, name, created, error = handler.add_account_option("C6 Pessoal")

        self.assertTrue(success, error)
        self.assertEqual(name, "C6 Pessoal")
        self.assertTrue(created)
        self.assertEqual(handler.service.spreadsheet.values_api.last_update["range"], "'Config'!G5")
        self.assertEqual(handler.service.spreadsheet.values_api.last_update["body"]["values"], [["C6 Pessoal"]])

    def test_unknown_bank_hint_does_not_select_existing_account(self):
        handler = SheetsHandler.__new__(SheetsHandler)
        handler.service = FakeService([HEADERS.copy()])
        handler.spreadsheet_id = "test-spreadsheet"
        handler._dashboard_cache = {}
        handler._write_lock = Lock()
        handler._processed_message_ids = set()
        handler._config_cache = None
        handler._config_cache_time = 0
        handler._config_options = lambda column_index: {4: ["Pix Itaú"], 6: ["Itaú"]}.get(column_index, [])
        handler.get_category_options = lambda: ["Mercado"]
        handler.get_meta_options = lambda: []

        with patch.dict("os.environ", {"SHEET_NAME": "Lançamentos"}):
            success, message = handler.add_transaction(
                "PF", "DESPESA", "Mercado", "Compra", 12,
                raw_message="Paguei 12 no Banco C6", bank="C6", account="",
            )

        self.assertFalse(success)
        self.assertIn("Conta", message)
        self.assertIsNone(handler.service.spreadsheet.values_api.last_batch)

    def test_ambiguous_payment_does_not_choose_first_menu_item(self):
        handler = SheetsHandler.__new__(SheetsHandler)
        handler.service = FakeService([HEADERS.copy()])
        handler.spreadsheet_id = "test-spreadsheet"
        handler._dashboard_cache = {}
        handler._write_lock = Lock()
        handler._processed_message_ids = set()
        handler._config_cache = None
        handler._config_cache_time = 0
        handler._config_options = lambda column_index: {
            4: ["Cartão de crédito Inter Empresas", "Cartão de crédito Inter Pessoal"],
            6: ["Itaú"],
        }.get(column_index, [])
        handler.get_category_options = lambda: ["Mercado"]
        handler.get_meta_options = lambda: []

        with patch.dict("os.environ", {"SHEET_NAME": "Lançamentos"}):
            success, message = handler.add_transaction(
                "PF", "DESPESA", "Mercado", "Compra", 12,
                raw_message="Paguei com cartão de crédito", account="Itaú",
                payment_method="Cartão de crédito",
            )

        self.assertFalse(success)
        self.assertIn("Forma de Pgto", message)
        self.assertIsNone(handler.service.spreadsheet.values_api.last_batch)


if __name__ == "__main__":
    unittest.main()
