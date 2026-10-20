import unittest
from unittest.mock import patch

import main


OPTIONS = {
    "categories": ["Alimentação", "Mercado", "Transporte"],
    "accounts": ["Nubank Pessoal", "Nubank Empresa", "Inter Pessoal"],
    "payment_methods": ["Pix Nubank Pessoal", "Pix Nubank Empresa", "Cartão de crédito Inter Pessoal"],
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

    def test_unknown_category_is_added_to_plan_and_used_for_transaction(self):
        no_optional_choices = {**OPTIONS, "accounts": [], "goals": []}
        transaction = {
            "tipo": "PF", "movimento": "DESPESA", "valor": 42.0,
            "categoria": None, "status": None, "raw_message": "Paguei R$ 42",
            "_ai_confidence": 0,
        }
        with patch.object(main.sheets, "get_transaction_options", return_value=no_optional_choices), \
             patch.object(main.sheets, "add_category_to_plan", return_value=(True, "Veterinário", True, "")) as add_category, \
             patch.object(main.sheets, "add_transaction", return_value=(True, "saved")) as save:
            first_reply = main._start_transaction("phone", transaction)
            self.assertIn("Qual categoria", first_reply)

            final_reply = main._continue_transaction("phone", "Veterinário")

        self.assertIn("Adicionei *Veterinário*", final_reply)
        self.assertIn("saved", final_reply)
        add_category.assert_called_once_with("Veterinário", "PF", "DESPESA")
        self.assertEqual(save.call_args.args[2], "Veterinário")
        self.assertTrue(save.call_args.kwargs["category_added"])
        self.assertNotIn("phone", main.pending_transactions)

    def test_failed_category_addition_returns_explanation_and_keeps_conversation(self):
        no_optional_choices = {**OPTIONS, "accounts": [], "goals": []}
        transaction = {
            "tipo": "PF", "movimento": "DESPESA", "valor": 42.0,
            "categoria": None, "status": None, "raw_message": "Paguei R$ 42",
            "_ai_confidence": 0,
        }
        with patch.object(main.sheets, "get_transaction_options", return_value=no_optional_choices), \
             patch.object(main.sheets, "add_category_to_plan", return_value=(False, "", False, "Não consegui editar a planilha.")):
            main._start_transaction("phone", transaction)
            reply = main._continue_transaction("phone", "Veterinário")

        self.assertIn("Não consegui editar a planilha", reply)
        self.assertEqual(main.pending_transactions["phone"]["awaiting"], "categoria")

    def test_asks_pf_or_pj_when_scope_is_not_clear(self):
        no_optional_choices = {**OPTIONS, "accounts": [], "payment_methods": [], "goals": []}
        transaction = {
            "tipo": None, "movimento": "DESPESA", "valor": 10,
            "categoria": "Mercado", "status": "Pago",
            "raw_message": "Paguei R$ 10 no mercado", "_ai_confidence": 1.0,
        }
        with patch.object(main.sheets, "get_transaction_options", return_value=no_optional_choices), \
             patch.object(main.sheets, "add_transaction", return_value=(True, "saved")) as save:
            question = main._start_transaction("phone", transaction)
            self.assertIn("PF", question)
            self.assertEqual(main.pending_transactions["phone"]["awaiting"], "tipo")
            reply = main._continue_transaction("phone", "PJ")

        self.assertEqual(reply, "saved")
        self.assertEqual(save.call_args.args[0], "PJ")

    def test_unknown_bank_is_not_mapped_to_another_existing_account(self):
        one_account = {**OPTIONS, "accounts": ["Itaú"], "payment_methods": [], "goals": []}
        transaction = {
            "tipo": "PF", "movimento": "DESPESA", "valor": 18,
            "categoria": "Mercado", "status": "Pago", "raw_message": "Paguei R$ 18 na farmácia com Banco C6",
            "banco": "C6", "_ai_confidence": 1.0,
        }
        with patch.object(main.sheets, "get_transaction_options", return_value=one_account), \
             patch.object(main.sheets, "add_account_option", return_value=(True, "C6 Pessoal", True, "")) as add_account, \
             patch.object(main.sheets, "add_transaction", return_value=(True, "saved")) as save:
            question = main._start_transaction("phone", transaction)
            self.assertIn("C6", question)
            self.assertEqual(main.pending_transactions["phone"]["awaiting"], "conta")
            reply = main._continue_transaction("phone", "C6 Pessoal")

        self.assertIn("Adicionei a conta", reply)
        self.assertEqual(reply.splitlines()[-1], "saved")
        add_account.assert_called_once_with("C6 Pessoal")
        self.assertEqual(save.call_args.kwargs["account"], "C6 Pessoal")

    def test_unknown_payment_method_is_confirmed_and_added_to_menu(self):
        options = {**OPTIONS, "goals": []}
        transaction = {
            "tipo": "PF", "movimento": "DESPESA", "valor": 25,
            "categoria": "Mercado", "conta": "Nubank Pessoal", "banco": "Nubank",
            "forma_pagamento": "Apple Pay", "status": "Pago",
            "raw_message": "Paguei R$ 25 no mercado com Apple Pay", "_ai_confidence": 1.0,
        }
        with patch.object(main.sheets, "get_transaction_options", return_value=options), \
             patch.object(main.sheets, "add_payment_method_option", return_value=(True, "Apple Pay Nubank Pessoal", True, "")) as add_method, \
             patch.object(main.sheets, "add_transaction", return_value=(True, "saved")) as save:
            question = main._start_transaction("phone", transaction)
            self.assertIn("Apple Pay", question)
            self.assertEqual(main.pending_transactions["phone"]["awaiting"], "forma_pagamento")
            reply = main._continue_transaction("phone", "SIM")

        self.assertIn("Adicionei", reply)
        self.assertEqual(reply.splitlines()[-1], "saved")
        add_method.assert_called_once_with("Apple Pay Nubank Pessoal")
        self.assertEqual(save.call_args.kwargs["payment_method"], "Apple Pay Nubank Pessoal")

    def test_ambiguous_payment_is_not_assigned_to_first_menu_item(self):
        methods = ["Cartão de crédito Inter Empresa", "Cartão de crédito Inter Pessoal"]
        self.assertIsNone(main._match_payment_option("Cartão de crédito", methods))
        self.assertEqual(main._match_payment_option("Cartão de crédito", methods, "Inter Pessoal"), "Cartão de crédito Inter Pessoal")

    def test_failed_save_keeps_transaction_pending_for_retry(self):
        no_optional_choices = {**OPTIONS, "accounts": [], "payment_methods": [], "goals": []}
        transaction = {
            "tipo": "PF", "movimento": "DESPESA", "valor": 10,
            "categoria": "Mercado", "status": "Pago", "raw_message": "Paguei R$ 10 no mercado",
            "_ai_confidence": 1.0,
        }
        with patch.object(main.sheets, "get_transaction_options", return_value=no_optional_choices), \
             patch.object(main.sheets, "add_transaction", side_effect=[(False, "Sheets indisponível"), (True, "saved")]) as save:
            first_reply = main._start_transaction("phone", transaction)
            self.assertIn("TENTAR NOVAMENTE", first_reply)
            self.assertEqual(main.pending_transactions["phone"]["awaiting"], "retry_save")
            final_reply = main._continue_transaction("phone", "TENTAR NOVAMENTE")

        self.assertEqual(final_reply, "saved")
        self.assertEqual(save.call_count, 2)
        self.assertNotIn("phone", main.pending_transactions)


if __name__ == "__main__":
    unittest.main()
