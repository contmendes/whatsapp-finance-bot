import unittest
from unittest.mock import MagicMock

from sheets_handler import SheetsHandler


class ProfileSheetTests(unittest.TestCase):
    def make_handler(self):
        handler = SheetsHandler.__new__(SheetsHandler)
        handler.service = MagicMock()
        handler.spreadsheet_id = "sheet-id"
        handler.service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [
                {"properties": {"title": "Lançamentos"}},
                {"properties": {"title": "Perfis"}},
            ]
        }
        values_api = handler.service.spreadsheets.return_value.values.return_value
        values_api.get.return_value.execute.return_value = {"values": []}
        values_api.update.return_value.execute.return_value = {}
        values_api.append.return_value.execute.return_value = {}
        return handler, values_api

    def test_existing_empty_profile_sheet_gets_headers_without_duplicate_add_sheet(self):
        handler, values_api = self.make_handler()

        profile = handler.get_profile("5511999999999", "Mariana")

        self.assertEqual(profile["name"], "Mariana")
        handler.service.spreadsheets.return_value.batchUpdate.assert_not_called()
        values_api.update.assert_called_once_with(
            spreadsheetId="sheet-id",
            range="'Perfis'!A1:E1",
            valueInputOption="USER_ENTERED",
            body={"values": [["Telefone", "Nome", "Tipo padrão", "Contexto atual", "Empresa"]]},
        )
        values_api.append.assert_called_once_with(
            spreadsheetId="sheet-id",
            range="'Perfis'!A:E",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [["5511999999999", "Mariana", "PF", "PF", ""]]},
        )

    def test_existing_profile_is_read_without_appending_duplicate(self):
        handler, values_api = self.make_handler()
        values_api.get.return_value.execute.return_value = {
            "values": [
                ["Telefone", "Nome", "Tipo padrão", "Contexto atual", "Empresa"],
                ["5511999999999", "Mariana", "PJ", "PJ", "Empresa X"],
            ]
        }

        profile = handler.get_profile("5511999999999", "Contato")

        self.assertEqual(profile, {
            "phone": "5511999999999",
            "name": "Mariana",
            "default_type": "PJ",
            "context": "PJ",
            "company": "Empresa X",
        })
        values_api.update.assert_not_called()
        values_api.append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
