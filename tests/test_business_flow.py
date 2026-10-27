import unittest
from threading import Lock
from unittest.mock import Mock

from business_flow import BusinessFlow
from sheets_handler import SheetsHandler
from whatsapp_handler import WhatsAppHandler


class FakeRequest:
    def __init__(self, result=None):
        self.result = result or {}

    def execute(self):
        return self.result


class FakeValues:
    def __init__(self, rows):
        self.rows = rows
        self.last_batch = None
        self.last_range = None

    def get(self, **kwargs):
        self.last_range = kwargs.get("range")
        return FakeRequest({"values": self.rows})

    def batchUpdate(self, **kwargs):
        self.last_batch = kwargs["body"]
        return FakeRequest({})


class FakeSpreadsheet:
    def __init__(self, values):
        self.values_api = values

    def values(self):
        return self.values_api


class FakeService:
    def __init__(self, rows):
        self.values_api = FakeValues(rows)
        self.spreadsheet = FakeSpreadsheet(self.values_api)

    def spreadsheets(self):
        return self.spreadsheet


class BusinessFlowTests(unittest.TestCase):
    def test_lists_centers_then_plan_and_saves_full_message(self):
        sheets = Mock()
        sheets.get_business_plan_options.return_value = ["Contratos", "Material de escritório"]
        sheets.add_business_transaction.return_value = (True, "saved")
        gemini = Mock()
        gemini.configured = True
        gemini.interpret_business_text.return_value = ({
            "plano_conta": "Contratos", "descricao_tipo": "contratos Isabella",
            "movimento": "DESPESA", "valor": 30, "data": None, "confidence": 0.95,
        }, None)
        flow = BusinessFlow(
            sheets, gemini, WhatsAppHandler(None, None),
            ["Caixa Geral Niterói", "Região dos Lagos"],
        )

        prompt = flow.process("551199", "Paguei R$ 30 para contratos Isabella", "msg-1")
        self.assertIn("1. Caixa Geral Niterói", prompt)
        self.assertIn("2. Região dos Lagos", prompt)
        self.assertEqual(flow.pending["551199"]["awaiting"], "center")

        answer = flow.process("551199", "1")
        self.assertEqual(answer, "saved")
        sheets.add_business_transaction.assert_called_once()
        call = sheets.add_business_transaction.call_args.kwargs
        self.assertEqual(call["center_sheet"], "Caixa Geral Niterói")
        self.assertEqual(call["plan_account"], "Contratos")
        self.assertEqual(call["type_description"], "contratos Isabella")
        self.assertEqual(call["description"], "Paguei R$ 30 para contratos Isabella")
        self.assertEqual(call["value"], 30.0)
        self.assertNotIn("551199", flow.pending)

    def test_missing_value_is_asked_instead_of_saving(self):
        sheets = Mock()
        flow = BusinessFlow(sheets, Mock(configured=False), WhatsAppHandler(None, None), ["Caixa Geral Niterói"])
        prompt = flow.process("551199", "paguei pelos contratos Isabella")
        self.assertIn("valor", prompt.casefold())
        sheets.add_business_transaction.assert_not_called()

    def test_low_confidence_plan_classification_shows_menu(self):
        sheets = Mock()
        sheets.get_business_plan_options.return_value = ["Contratos", "Material de escritório"]
        gemini = Mock()
        gemini.configured = True
        gemini.interpret_business_text.return_value = ({
            "plano_conta": "Contratos", "descricao_tipo": "contratos Isabella",
            "movimento": "DESPESA", "valor": 30, "confidence": 0.4,
        }, None)
        flow = BusinessFlow(sheets, gemini, WhatsAppHandler(None, None), ["Caixa Geral Niterói"])
        prompt = flow.process("551199", "paguei R$ 30 para contratos Isabella")
        self.assertIn("1. Contratos", prompt)
        self.assertEqual(flow.pending["551199"]["awaiting"], "plan_account")
        sheets.add_business_transaction.assert_not_called()


class BusinessSheetWriteTests(unittest.TestCase):
    def test_writes_only_business_input_columns(self):
        # B:K contains a prior row and formula values in C/D/J/K.
        row = ["15/09/2026", "formula grupo", "formula subgrupo", "Contratos", "Tipo antigo",
               "Descrição antiga", "", 10, "formula J", "formula K"]
        handler = SheetsHandler.__new__(SheetsHandler)
        handler.service = FakeService([row])
        handler.spreadsheet_id = "test-business-sheet"
        handler._write_lock = Lock()
        handler._processed_message_ids = set()

        success, message = handler.add_business_transaction(
            center_sheet="Caixa Geral Niterói", movement="DESPESA", plan_account="Contratos",
            type_description="contratos Isabella", description="Paguei R$ 30 para contratos Isabella",
            value=30, transaction_date="2026-10-27", message_id="business-msg",
            plan_options=["Contratos", "Material de escritório"],
            allowed_centers=["Caixa Geral Niterói", "Região dos Lagos"],
        )

        self.assertTrue(success, message)
        writes = handler.service.values_api.last_batch["data"]
        ranges = {write["range"] for write in writes}
        self.assertEqual(ranges, {
            "'Caixa Geral Niterói'!B4", "'Caixa Geral Niterói'!E4",
            "'Caixa Geral Niterói'!F4", "'Caixa Geral Niterói'!G4",
            "'Caixa Geral Niterói'!I4",
        })
        self.assertEqual(handler.service.values_api.last_batch["valueInputOption"], "USER_ENTERED")
        cells = {write["range"].split("!")[1]: write["values"][0][0] for write in writes}
        self.assertEqual(cells["B4"], "27/10/2026")
        self.assertEqual(cells["E4"], "Contratos")
        self.assertEqual(cells["F4"], "contratos Isabella")
        self.assertEqual(cells["G4"], "Paguei R$ 30 para contratos Isabella")
        self.assertEqual(cells["I4"], 30.0)

    def test_rejects_center_outside_allowlist_without_writing(self):
        handler = SheetsHandler.__new__(SheetsHandler)
        handler.service = FakeService([])
        handler.spreadsheet_id = "test-business-sheet"
        handler._write_lock = Lock()
        handler._processed_message_ids = set()
        success, _ = handler.add_business_transaction(
            center_sheet="Outra aba", movement="DESPESA", plan_account="Contratos",
            type_description="contratos Isabella", description="texto", value=30,
            plan_options=["Contratos"], allowed_centers=["Caixa Geral Niterói"],
        )
        self.assertFalse(success)
        self.assertIsNone(handler.service.values_api.last_batch)


if __name__ == "__main__":
    unittest.main()
