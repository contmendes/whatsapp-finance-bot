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

    def get(self, **kwargs):
        return FakeRequest({"values": self.rows})

    def batchUpdate(self, **kwargs):
        self.last_batch = kwargs["body"]
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

    def test_append_follows_last_transaction_and_only_writes_input_columns(self):
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
        handler._config_options = lambda column_index: []
        handler._resolve_category = lambda category, scope, movement: category

        with patch.dict("os.environ", {"SHEET_NAME": "Lançamentos"}):
            success, _ = handler.add_transaction(
                "PF", "DESPESA", "Mercado", "Compra nova", 42.50,
                phone_number="5511999999999", contact_name="Contato",
                message_id="new-message",
            )

        self.assertTrue(success)
        body = handler.service.spreadsheet.values_api.last_batch
        self.assertIsNotNone(body)
        writes = body["data"]
        self.assertTrue(writes)
        self.assertTrue(all(entry["range"].endswith("54") for entry in writes))
        written_columns = {column_from_range(entry["range"]) for entry in writes}
        self.assertEqual(written_columns, {"A", "D", "E", "H", "I", "J", "K", "L", "P", "Q"})
        self.assertTrue(written_columns.isdisjoint({"B", "C", "F", "G", "M", "N", "O"}))


if __name__ == "__main__":
    unittest.main()
