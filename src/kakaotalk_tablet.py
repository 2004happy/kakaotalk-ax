"""Independent Android tablet authentication and LOCO sending; no app/UI automation."""

import argparse
import getpass
import hashlib
import http.client
import json
import os
import plistlib
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from bson import Int64

from kakaotalk_loco import LocoError, Session
from kakaotalk_reader import KAKAO_PLIST_PATH, load_config
from kakaotalk_service import Reader, number

VERSION = "25.9.2"
AGENT = "android/" + VERSION + "/ko"
USER_AGENT = "KT/" + VERSION + " An/13 ko"
MODEL = "SM-T870"
TABLET_PATH = Path.home() / ".config/kakaotalk-reader/tablet-session.json"
PENDING_PATH = TABLET_PATH.with_name("tablet-pending-device.json")


def save_private(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tablet-session-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(value, file)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_tablet(value):
    save_private(TABLET_PATH, value)


def load_tablet():
    descriptor = os.open(TABLET_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as file:
        metadata = os.fstat(file.fileno())
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077 or metadata.st_size > 16384:
            raise LocoError("invalid_private_tablet_file")
        value = json.load(file)
    validate_tablet(value)
    return value


def validate_tablet(value):
    if (value.get("device_type") != "tablet" or value.get("os") != "android" or
            value.get("version") != VERSION or value.get("model") != MODEL or
            not re.fullmatch(r"[0-9a-f]{64}", value.get("device", ""))):
        raise LocoError("tablet_profile_required_no_pc_fallback")
    if value.get("uid") != load_config().get("user_id"):
        raise LocoError("tablet_account_mismatch")
    token = value.get("token")
    if not isinstance(token, str) or not 20 <= len(token) <= 1024 or any(c.isspace() for c in token):
        raise LocoError("invalid_tablet_token")


def tablet_status():
    try:
        value = load_tablet()
        return {"status": "ready" if value.get("connection_verified") else "verification_required",
                "deviceType": "tablet", "os": "android", "uiAutomation": False,
                "sendingSupported": bool(value.get("connection_verified")),
                "coexistenceObservation": value.get("coexistence_observation")}
    except (OSError, ValueError, LocoError):
        return {"status": "tablet_login_required", "deviceType": "tablet", "uiAutomation": False,
                "sendingSupported": False, "setupCommand": "kakaotalk-tablet login"}


def account_request(endpoint, email, fields, as_json=False):
    if endpoint not in ("login.json", "passcodeLogin/generate", "passcodeLogin/registerDevice"):
        raise LocoError("unsupported_android_auth_endpoint")
    xvc = hashlib.sha512(("BARD|" + USER_AGENT + "|DANTE|" + email + "|SIAN").encode()).hexdigest()[:16]
    headers = {"A": AGENT, "User-Agent": USER_AGENT, "X-VC": xvc, "Accept-Language": "ko",
               "Content-Type": "application/json; charset=utf-8" if as_json else "application/x-www-form-urlencoded"}
    body = json.dumps(fields) if as_json else urllib.parse.urlencode(fields)
    connection = http.client.HTTPSConnection("katalk.kakao.com", timeout=12)
    try:
        connection.request("POST", "/android/account/" + endpoint, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(1048577)
        if len(raw) > 1048576 or response.status != 200:
            raise LocoError("android_auth_http_" + str(response.status))
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise LocoError("invalid_android_auth_response")
        return decoded
    finally:
        connection.close()


def android_login(email, password, device, replace_tablet=False):
    return account_request("login.json", email, {
        "email": email, "password": password, "device_uuid": device,
        "device_name": MODEL, "model_name": MODEL, "permanent": "true",
        "forced": "true" if replace_tablet else "false",
    })


def register_tablet(email, password, device):
    response = account_request("passcodeLogin/generate", email, {
        "email": email, "password": password, "permanent": True,
        "device": {"name": MODEL, "uuid": device, "model": MODEL, "osVersion": "33"},
    }, as_json=True)
    passcode = response.get("passcode")
    if not isinstance(passcode, str) or not re.fullmatch(r"\d{4,12}", passcode):
        raise LocoError("tablet_passcode_status_" + str(response.get("status", "missing")))
    # This command requires a user-owned terminal. Codes never enter MCP output or files.
    print("휴대폰 카카오톡에서 태블릿 기기 등록을 승인하고 다음 코드를 입력하세요: " + passcode, flush=True)
    deadline = time.monotonic() + min(300, max(30, int(response.get("remainingSeconds") or 120)))
    for _ in range(20):
        if time.monotonic() >= deadline:
            break
        registered = account_request("passcodeLogin/registerDevice", email, {
            "email": email, "password": password, "device": {"uuid": device},
        }, as_json=True)
        if registered.get("status") == 0:
            return
        if registered.get("status") != -100:
            raise LocoError("tablet_registration_status_" + str(registered.get("status", "missing")))
        interval = registered.get("nextRequestIntervalInSeconds")
        if type(interval) is not int or interval <= 0:
            raise LocoError("invalid_tablet_registration_interval")
        time.sleep(min(interval, 30, max(0, deadline - time.monotonic())))
    raise LocoError("tablet_registration_timeout")


def desktop_connections():
    """Observe OS socket metadata only, without AX, input events, or app calls."""
    try:
        pids = subprocess.run(["pgrep", "-x", "KakaoTalk"], capture_output=True, text=True, timeout=3).stdout.split()
        if len(pids) != 1:
            return set()
        result = subprocess.run(["lsof", "-nP", "-a", "-p", pids[0], "-iTCP", "-sTCP:ESTABLISHED", "-Fn"],
                                capture_output=True, text=True, timeout=5)
        # Keep the tuple local; only counts are returned to the caller/user.
        ports = {"995", "5242", "10009", "8080", "5223", "5228", "9282"}
        return {line for line in result.stdout.splitlines() if line.startswith("n") and "->" in line
                and line.rsplit(":", 1)[-1] in ports}
    except (OSError, subprocess.SubprocessError):
        return set()


class TabletSession(Session):
    def connect(self):
        validate_tablet(self.credentials)
        # The disabled Mac Session.connect is deliberately never called.
        self._connect_profile("android")


def login_interactive(replace_tablet=False):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return {"status": "user_input_required", "reason": "one_time_plaintext_password_and_phone_approval",
                "command": "kakaotalk-tablet login" + (" --replace-tablet" if replace_tablet else ""), "networkRequests": 0}
    try:
        preferences = plistlib.loads(Path(KAKAO_PLIST_PATH).read_bytes())
        default_email = preferences.get("Email", "")
    except (OSError, ValueError):
        default_email = ""
    email = input("카카오계정 이메일" + (" [" + default_email + "]" if default_email else "") + ": ").strip() or default_email
    if not email:
        raise LocoError("email_required")
    password = getpass.getpass("카카오계정 비밀번호 (저장하지 않음): ")
    if not password:
        raise LocoError("password_required")
    try:
        device = load_tablet()["device"]
    except (OSError, ValueError, LocoError):
        try:
            pending = json.loads(PENDING_PATH.read_text())
            device = pending["device"]
            if pending.get("uid") != load_config().get("user_id") or not re.fullmatch(r"[0-9a-f]{64}", device):
                raise ValueError("invalid pending tablet")
        except (OSError, ValueError, KeyError):
            device = secrets.token_hex(32)
            save_private(PENDING_PATH, {"device": device, "uid": load_config().get("user_id")})
    before = desktop_connections()
    try:
        result = android_login(email, password, device, replace_tablet)
        if result.get("status") == -100:
            register_tablet(email, password, device)
            result = android_login(email, password, device, replace_tablet)
    finally:
        password = None
    if result.get("status") != 0:
        raise LocoError("tablet_login_status_" + str(result.get("status", "missing")))
    value = {"device_type": "tablet", "os": "android", "model": MODEL, "version": VERSION,
             "device": device, "uid": result.get("userId"), "token": result.get("access_token"),
             "refresh_token": result.get("refresh_token"), "connection_verified": False}
    validate_tablet(value)
    save_tablet(value)
    return verify_connection(value, before)


def verify_connection(value=None, before=None):
    """Reconnect the registered tablet and observe desktop TCP continuity; no REST login."""
    value = load_tablet() if value is None else value
    before = desktop_connections() if before is None else before
    session = TabletSession(value)
    try:
        session.connect()
        retained = set(before)
        for delay in (3, 4, 5):
            time.sleep(delay)
            after = desktop_connections()
            retained.intersection_update(after)
        observation = {"beforeSocketCount": len(before), "afterSocketCount": len(after),
                       "sameSocketsRetained": len(retained), "observedSeconds": 12, "uiStateVerified": False}
        value["coexistence_observation"] = observation
        if not retained:
            save_tablet(value)
            return {"status": "coexistence_verification_required", "ownAccountVerified": True,
                    "messagesSent": 0, "coexistenceObservation": observation,
                    "note": "After checking the desktop app is still logged in, run: kakaotalk-tablet confirm-desktop"}
        value["connection_verified"] = True
        save_tablet(value)
        return {"status": "tablet_connected", "ownAccountVerified": True, "messagesSent": 0,
                "coexistenceObservation": observation,
                "note": "Socket continuity is indirect evidence; check the app still shows your chat list."}
    finally:
        session.close()


def send_message(room_id, text):
    number(room_id, "room_id", 1)
    if not isinstance(text, str) or not text.strip() or "\0" in text or len(text.encode()) > 10000:
        raise ValueError("text must contain 1–10000 UTF-8 bytes and no NUL")
    try:
        credentials = load_tablet()
    except (OSError, ValueError, LocoError):
        return {"status": "tablet_login_required", "messagesSent": 0,
                "setupCommand": "kakaotalk-tablet login"}
    if not credentials.get("connection_verified"):
        return {"status": "tablet_verification_required", "messagesSent": 0}
    room = Reader().find_room(room_id=room_id)
    session = TabletSession(credentials)
    try:
        session.connect()
        try:
            result = session.command("WRITE", {"chatId": Int64(room_id), "msg": text, "type": 1, "noSeen": False})
        except Exception as error:
            rejected = isinstance(error, LocoError) and str(error).startswith("WRITE_status_")
            return {"status": "rejected" if rejected else "outcome_unknown", "retrySafe": False,
                    "error": str(error) if isinstance(error, LocoError) else type(error).__name__}
        log_id = result.get("logId")
        accepted = isinstance(log_id, int) and log_id > 0
        return {"status": "sent" if accepted else "accepted_unverified", "transport": "loco_tablet_tls", "roomId": room_id,
                "roomName": room["displayName"], "logId": str(log_id) if accepted else None, "text": text,
                "serverAcknowledged": True, "retrySafe": False}
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login", "status", "verify", "confirm-desktop"])
    parser.add_argument("--replace-tablet", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "confirm-desktop":
            value = load_tablet()
            if not value.get("coexistence_observation"):
                raise LocoError("run_tablet_login_first")
            value["connection_verified"] = True
            value["coexistence_observation"]["userConfirmedDesktopLogin"] = True
            save_tablet(value)
            result = tablet_status()
        elif args.command == "verify":
            result = verify_connection()
        else:
            result = login_interactive(args.replace_tablet) if args.command == "login" else tablet_status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error) if isinstance(error, LocoError) else type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
