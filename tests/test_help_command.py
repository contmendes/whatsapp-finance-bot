import unittest
from unittest.mock import patch

import main


class HelpCommandTests(unittest.TestCase):
    def test_help_is_sent_even_when_a_transaction_is_pending(self):
        phone = "5511999999999"
        pending = {"awaiting": "tipo", "raw_message": "gastei 50 reais"}

        with patch.dict(main.pending_transactions, {phone: pending}, clear=True):
            with patch.object(main.whatsapp, "get_help_message", return_value="Ajuda"):
                with patch("main._send_whatsapp_reply") as send_reply:
                    with patch.object(main.sheets, "get_profile") as get_profile:
                        main.process_user_message(phone, "AJUDA")
            self.assertEqual(main.pending_transactions[phone], pending)

        send_reply.assert_called_once_with(phone, "Ajuda", "ajuda")
        get_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
