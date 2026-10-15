import unittest
from unittest.mock import patch

import main


OPTIONS = {
    "categories": ["Alimentação", "Mercado", "Transporte"],
    "accounts": ["Nubank Pessoal", "Nubank Empresa", "Inter Pessoal"],
    "goals": ["Reserva", "Viagem"],
    "statuses": ["Recebido", "A receber", "Pago", "A pagar"],
}


class TransactionConversationTests(unittest.TestCase):
    def tearDown(self):
        main.pending_transactions.clear()

    def test_asks_for_meta_then_saves_full_message_with_valid_account(self):
        original = "Paguei R$ 25,00 no almoço na categoria Alimentação pelo Nubank"
        transaction = {
            "tipo": "PF",
            "movimento": "DESPESA",
            "valor": 25.0,
            "data": "2026-10-14",
            "categoria": "Alimentação",
            "conta": None,
            "meta": None,
            "status": None,
            "descricao": "Almoço",
            "raw_message": original,
            "banco": "Nubank",
            "forma_pagamento": "Pix",
            "_ai_confidence": 0,
            "message_id": "wamid-original",
            "contact_name": "Contato",
        }
        with patch.object(main.sheets, "get_transaction_options", return_value=OPTIONS), \
             patch.object(main.sheets, "add_transaction", return_value=(True, "saved")) as save:
            first_reply = main._start_transaction("5511999999999", transaction)
            self.assertIn("A qual meta", first_reply)
            self.assertEqual(main.pending_transactions["5511999999999"]["awaiting"], "meta")

            final_reply = main._continue_transaction("5511999999999", "Reserva")

        self.assertEqual(final_reply, "saved")
        self.assertNotIn("5511999999999", main.pending_transactions)
        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs["raw_message"], original)
        self.assertEqual(save.call_args.kwargs["account"], "Nubank Pessoal")
        self.assertEqual(save.call_args.kwargs["meta"], "Reserva")
        self.assertEqual(save.call_args.kwargs["status"], "Pago")

    def test_pending_or_completed_status_is_inferred_from_plain_language(self):
        self.assertEqual(main._infer_status("Paguei a conta", "DESPESA"), "Pago")
        self.assertEqual(main._infer_status("Vou pagar a conta amanhã", "DESPESA"), "A pagar")
        self.assertEqual(main._infer_status("Recebi o salário", "RECEITA"), "Recebido")
        self.assertEqual(main._infer_status("Vou receber na sexta", "RECEITA"), "A receber")

    def test_asks_for_category_when_model_candidate_is_below_confidence(self):
        transaction = {
            "tipo": "PF", "movimento": "DESPESA", "valor": 10,
            "categoria": "Mercado", "raw_message": "gastei dez reais",
            "_ai_confidence": 0.2,
        }
        with patch.object(main.sheets, "get_transaction_options", return_value=OPTIONS):
            reply = main._start_transaction("phone", transaction)
        self.assertIn("Qual categoria", reply)
        self.assertEqual(main.pending_transactions["phone"]["awaiting"], "categoria")

    def test_expense_cannot_use_income_status(self):
        no_optional_choices = {**OPTIONS, "accounts": [], "goals": []}
        transaction = {
            "tipo": "PF", "movimento": "DESPESA", "valor": 10,
            "categoria": "Mercado", "status": "Recebido",
            "raw_message": "Mercado 10", "_ai_confidence": 1.0,
        }
        with patch.object(main.sheets, "get_transaction_options", return_value=no_optional_choices), \
             patch.object(main.sheets, "add_transaction", return_value=(True, "saved")) as save:
            reply = main._start_transaction("phone", transaction)
            self.assertIn("Pago / A pagar", reply)
            self.assertEqual(main.pending_transactions["phone"]["awaiting"], "status")
            invalid = main._continue_transaction("phone", "Recebido")
            self.assertIn("Pago / A pagar", invalid)
            result = main._continue_transaction("phone", "Pago")

        self.assertEqual(result, "saved")
        self.assertEqual(save.call_args.kwargs["status"], "Pago")


if __name__ == "__main__":
    unittest.main()
