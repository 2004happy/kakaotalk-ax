import unittest
from unittest.mock import Mock, patch

import kakaotalk_loco as loco


class SelfSendTests(unittest.TestCase):
    def setUp(self):
        self.block_patch = patch.object(loco, "LIVE_LOCO_DISABLED", False)
        self.block_patch.start()
        self.addCleanup(self.block_patch.stop)
        self.credentials = {"uid": 42, "device": "test-device", "token": "test-token", "version": "26.6.1"}
        self.reader_patch = patch.object(loco, "Reader")
        self.reader = self.reader_patch.start().return_value
        self.reader.all_rooms.return_value = [
            {"type": 1, "chatId": 1, "displayName": "나와의 채팅"},
            {"type": 5, "chatId": 2, "displayName": "이름"},
        ]
        self.credentials_patch = patch.object(loco, "load_session", return_value=self.credentials)
        self.credentials_patch.start()
        self.session_patch = patch.object(loco, "Session")
        self.session = self.session_patch.start().return_value
        self.addCleanup(self.reader_patch.stop)
        self.addCleanup(self.credentials_patch.stop)
        self.addCleanup(self.session_patch.stop)

    def test_self_type_selected_and_ack_is_checked_against_server_history(self):
        self.session.command.side_effect = [
            {"chatInfo": {"type": "MemoChat"}}, {"logId": 50},
            {"chatLogs": [{"logId": 50, "authorId": 42, "message": "테스트"}]},
        ]
        result = loco.send_self("테스트")
        self.assertEqual(result["roomId"], 2)
        self.assertTrue(result["serverHistoryVerified"])
        self.assertEqual([call.args[0] for call in self.session.command.call_args_list],
                         ["CHATONROOM", "WRITE", "SYNCMSG"])
        self.session.close.assert_called_once()

    def test_server_non_self_room_is_never_sent_to(self):
        self.session.command.return_value = {"chatInfo": {"type": "MultiChat"}}
        with self.assertRaisesRegex(loco.LocoError, "server_self_room_not_verified"):
            loco.send_self("테스트")
        self.assertEqual(self.session.command.call_count, 1)
        self.session.close.assert_called_once()

    def test_lost_ack_is_unknown_and_never_retried(self):
        self.session.command.side_effect = [{"t": "MemoChat"}, TimeoutError()]
        result = loco.send_self("테스트")
        self.assertEqual(result["status"], "outcome_unknown")
        self.assertFalse(result["retrySafe"])
        self.assertEqual(self.session.command.call_count, 2)

    def test_wrong_author_or_text_does_not_count_as_verified(self):
        self.session.command.side_effect = [
            {"t": "MemoChat"}, {"logId": 50},
            {"chatLogs": [{"logId": 50, "authorId": 99, "message": "테스트"},
                          {"logId": 50, "authorId": 42, "message": "다른 내용"}]},
        ]
        self.assertFalse(loco.send_self("테스트")["serverHistoryVerified"])

    def test_null_history_is_unverified_without_a_second_write(self):
        self.session.command.side_effect = [{"t": "MemoChat"}, {"logId": 50}, {"chatLogs": None}]
        result = loco.send_self("테스트")
        self.assertEqual(result["status"], "sent")
        self.assertFalse(result["serverHistoryVerified"])
        self.assertIsNone(result["verificationError"])
        self.assertEqual(self.session.command.call_count, 3)

    def test_authentication_failure_closes_connection_without_write(self):
        self.session.connect.side_effect = loco.LocoError("authenticated_user_mismatch")
        with self.assertRaisesRegex(loco.LocoError, "authenticated_user_mismatch"):
            loco.send_self("테스트")
        self.session.command.assert_not_called()
        self.session.close.assert_called_once()

    def test_invalid_text_and_ambiguous_self_rooms_stop_before_connect(self):
        for text in ["", "   ", "a\0b", "가" * 4000]:
            with self.subTest(text_length=len(text)), self.assertRaises(ValueError):
                loco.send_self(text)
        self.reader.all_rooms.return_value = [{"type": 5, "chatId": 1}, {"type": 5, "chatId": 2}]
        with self.assertRaisesRegex(loco.LocoError, "self_room_not_unique"):
            loco.send_self("테스트")
        self.session.connect.assert_not_called()


class SessionConflictTests(unittest.TestCase):
    def test_default_self_send_stops_before_database_credentials_and_network(self):
        with patch.object(loco, "Reader") as reader, patch.object(loco, "load_session") as credentials, \
                patch.object(loco, "dial") as dial:
            result = loco.send_self("테스트")
            self.assertEqual(result["status"], "disabled")
            self.assertEqual(result["messagesSent"], 0)
            reader.assert_not_called()
            credentials.assert_not_called()
            dial.assert_not_called()

    def test_direct_session_login_is_also_disabled(self):
        with patch.object(loco, "dial") as dial:
            with self.assertRaisesRegex(loco.LocoError, "disabled_due_to_desktop_session_conflict"):
                loco.Session({}).connect()
            dial.assert_not_called()


if __name__ == "__main__":
    unittest.main()
