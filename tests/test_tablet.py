import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import kakaotalk_loco as loco
import kakaotalk_tablet as tablet


def fixture():
    return {"device_type": "tablet", "os": "android", "model": "SM-T870", "version": tablet.VERSION,
            "device": "a" * 64, "uid": 42, "token": "t" * 65, "connection_verified": True}


class TabletProfileTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.object(tablet, "load_config", return_value={"user_id": 42})
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_pc_profiles_and_other_accounts_are_rejected(self):
        tablet.validate_tablet(fixture())
        for key, value in [("os", "mac"), ("device_type", "pc"), ("model", "Mac"),
                           ("device", "mac-device-id"), ("uid", 99), ("token", "bad\nheader")]:
            with self.subTest(key=key), self.assertRaises(loco.LocoError):
                tablet.validate_tablet({**fixture(), key: value})

    def test_all_loco_stages_use_android_with_pc_backend_still_disabled(self):
        dial = MagicMock()
        responses = [{"ticket": {"lsl": ["ticket.kakao.com"]}, "wifi": {"ports": [995]}},
                     {"host": "chat.kakao.com", "port": 5223}, {"userId": 42}]
        with patch.object(loco, "dial", dial), patch.object(loco, "request", side_effect=responses) as request:
            session = tablet.TabletSession(fixture())
            session.connect()
            self.assertTrue(loco.LIVE_LOCO_DISABLED)
            self.assertEqual([call.args[2] for call in request.call_args_list], ["GETCONF", "CHECKIN", "LOGINLIST"])
            self.assertTrue(all(call.args[3]["os"] == "android" for call in request.call_args_list))
            self.assertTrue(request.call_args_list[1].args[3]["useSub"])
            self.assertEqual(request.call_args_list[2].args[3]["duuid"], "a" * 64)
            session.close()

    def test_private_tablet_file_is_separate_and_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tablet-session.json"
            with patch.object(tablet, "TABLET_PATH", target):
                tablet.save_tablet(fixture())
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
                self.assertEqual(tablet.load_tablet()["device_type"], "tablet")
                os.chmod(target, 0o644)
                with self.assertRaises(loco.LocoError):
                    tablet.load_tablet()

    def test_noninteractive_setup_does_not_read_password_or_connect(self):
        with patch.object(tablet.sys.stdin, "isatty", return_value=False), \
                patch.object(tablet.getpass, "getpass") as password, \
                patch.object(tablet, "account_request") as request, patch.object(tablet, "save_private") as save:
            result = tablet.login_interactive(True)
            self.assertEqual(result["status"], "user_input_required")
            request.assert_not_called()
            password.assert_not_called()
            save.assert_not_called()

    def test_desktop_observer_requests_numeric_ports_to_avoid_service_name_mismatch(self):
        connection = "n127.0.0.1:12345->203.0.113.1:995"
        responses = [SimpleNamespace(stdout="123\n"),
                     SimpleNamespace(stdout="p123\n" + connection + "\nn127.0.0.1:12346->203.0.113.2:443\n")]
        with patch.object(tablet.subprocess, "run", side_effect=responses) as run:
            self.assertEqual(tablet.desktop_connections(), {connection})
            self.assertIn("-nP", run.call_args_list[1].args[0])

    def test_auth_can_only_call_android_endpoints_over_https(self):
        connection = MagicMock()
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"status":-100}'
        with patch.object(tablet.http.client, "HTTPSConnection", connection):
            tablet.android_login("test@example.com", "fixture-password", "a" * 64, False)
            connection.assert_called_once_with("katalk.kakao.com", timeout=12)
            args, kwargs = connection.return_value.request.call_args
            self.assertEqual(args[:2], ("POST", "/android/account/login.json"))
            self.assertEqual(kwargs["headers"]["A"], "android/" + tablet.VERSION + "/ko")
            self.assertIn("forced=false", kwargs["body"])
            with self.assertRaises(loco.LocoError):
                tablet.account_request("/mac/account/login.json", "test@example.com", {})

    def test_registration_only_enables_sending_when_desktop_sockets_stay_present(self):
        for retained in (True, False):
            with self.subTest(retained=retained), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                before = {"nlocal:12345->peer:5242"}
                after = before if retained else set()
                with patch.object(tablet, "TABLET_PATH", path / "tablet.json"), \
                        patch.object(tablet, "PENDING_PATH", path / "pending.json"), \
                        patch.object(tablet, "KAKAO_PLIST_PATH", str(path / "missing.plist")), \
                        patch.object(tablet.sys.stdin, "isatty", return_value=True), \
                        patch.object(tablet.sys.stdout, "isatty", return_value=True), \
                        patch("builtins.input", return_value="test@example.com"), \
                        patch.object(tablet.getpass, "getpass", return_value="fixture-password"), \
                        patch.object(tablet, "android_login", return_value={"status": 0, "userId": 42, "access_token": "t" * 65}) as login, \
                        patch.object(tablet, "TabletSession") as session, \
                        patch.object(tablet, "desktop_connections", side_effect=[before, after, after, after]), \
                        patch.object(tablet.time, "sleep"):
                    result = tablet.login_interactive(True)
                    self.assertEqual(result["messagesSent"], 0)
                    saved = json.loads((path / "tablet.json").read_text())
                    self.assertEqual(saved["connection_verified"], retained)
                    self.assertEqual(saved["os"], "android")
                    self.assertNotIn("password", saved)
                    self.assertNotIn("fixture-password", (path / "tablet.json").read_text())
                    login.assert_called_once()
                    session.return_value.command.assert_not_called()
                    session.return_value.close.assert_called_once()


class TabletSendTests(unittest.TestCase):
    def test_regular_direct_group_and_open_rooms_are_not_restricted_to_self(self):
        for room_type in [0, 1, 4]:
            room_id = 2**53 + 123
            with self.subTest(room_type=room_type), patch.object(tablet, "load_tablet", return_value=fixture()), \
                    patch.object(tablet, "Reader") as reader, patch.object(tablet, "TabletSession") as session:
                reader.return_value.find_room.return_value = {"chatId": room_id, "type": room_type, "displayName": "테스트 방"}
                session.return_value.command.return_value = {"logId": 2**60 + 1}
                result = tablet.send_message(room_id, "메시지")
                self.assertEqual(result["status"], "sent")
                reader.return_value.find_room.assert_called_once_with(room_id=room_id)
                call = session.return_value.command.call_args
                self.assertEqual(call.args[0], "WRITE")
                self.assertEqual(call.args[1]["chatId"], room_id)
                session.return_value.close.assert_called_once()

    def test_uncertain_delivery_is_never_retried(self):
        with patch.object(tablet, "load_tablet", return_value=fixture()), \
                patch.object(tablet, "Reader") as reader, patch.object(tablet, "TabletSession") as session:
            reader.return_value.find_room.return_value = {"displayName": "테스트 방"}
            session.return_value.command.side_effect = TimeoutError()
            result = tablet.send_message(123, "메시지")
            self.assertEqual(result["status"], "outcome_unknown")
            self.assertFalse(result["retrySafe"])
            self.assertEqual(session.return_value.command.call_count, 1)

    def test_missing_tablet_auth_never_falls_back_to_mac(self):
        with patch.object(tablet, "load_tablet", side_effect=FileNotFoundError()), \
                patch.object(tablet, "TabletSession") as session:
            self.assertEqual(tablet.send_message(123, "메시지")["status"], "tablet_login_required")
            session.assert_not_called()

    def test_unverified_session_cannot_send(self):
        with patch.object(tablet, "load_tablet", return_value={**fixture(), "connection_verified": False}), \
                patch.object(tablet, "TabletSession") as session:
            self.assertEqual(tablet.send_message(123, "메시지")["status"], "tablet_verification_required")
            session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
