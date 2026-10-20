import unittest
from threading import Lock
from unittest.mock import MagicMock

from sheets_handler import SheetsHandler


class CategoryCreationTests(unittest.TestCase):
    def make_handler(self, rows):
        handler = SheetsHandler.__new__(SheetsHandler)
        handler.service = MagicMock()
        handler.spreadsheet_id = "sheet-id"
        handler._write_lock = Lock()
        handler._config_cache = None
        handler._config_cache_time = 0
        handler._dashboard_cache = {}
        values_api = handler.service.spreadsheets.return_value.values.return_value
        values_api.get.return_value.execute.return_value = {"values": rows}
        values_api.update.return_value.execute.return_value = {}
        return handler, values_api

    def test_new_pf_expense_uses_an_unused_child_code(self):
        handler, values_api = self.make_handler([
            ["9.12.01", "Doações"],
            ["9.12.99", "Diversos"],
        ])

        result = handler.add_category_to_plan("Veterinário", "PF", "DESPESA")

        self.assertEqual(result, (True, "Veterinário", True, ""))
        values_api.update.assert_called_once_with(
            spreadsheetId="sheet-id",
            range="'Config'!A5:B5",
            valueInputOption="RAW",
            body={"values": [["9.12.02", "Veterinário"]]},
        )

    def test_new_pj_expense_uses_pj_code_prefix(self):
        handler, values_api = self.make_handler([["4.03.99", "Outras despesas com vendas"]])

        result = handler.add_category_to_plan("Assinatura nova", "PJ", "DESPESA")

        self.assertEqual(result, (True, "Assinatura nova", True, ""))
        self.assertEqual(values_api.update.call_args.kwargs["body"]["values"], [["4.99.01", "Assinatura nova"]])

    def test_existing_category_is_reused_without_writing(self):
        handler, values_api = self.make_handler([["9.12.03", "Veterinário"]])

        result = handler.add_category_to_plan("  VETERINÁRIO ", "PF", "DESPESA")

        self.assertEqual(result, (True, "Veterinário", False, ""))
        values_api.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
