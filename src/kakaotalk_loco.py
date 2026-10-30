"""TLS LOCO transport and one-shot self-chat sending, independent of the GUI."""

import json
import os
import socket
import ssl
import struct
import time
from pathlib import Path

from bson import BSON, Binary, Int64

from kakaotalk_reader import hashed_device_uuid, load_config
from kakaotalk_service import Reader

HEADER = struct.Struct("<Ih11sBI")
MAX_BODY = 1024 * 1024
SESSION_PATH = Path.home() / ".config/kakaotalk-reader/loco-session.json"
LIVE_LOCO_DISABLED = True
DISABLED_REASON = "disabled_due_to_desktop_session_conflict"


class LocoError(Exception):
    """Only fixed diagnostic codes, never response bodies or credentials."""


def load_session():
    config = load_config()
    descriptor = os.open(SESSION_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as file:
        metadata = os.fstat(file.fileno())
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077 or metadata.st_size > 16384:
            raise LocoError("invalid_private_session_file")
        session = json.load(file)
    if (session.get("uid") != config.get("user_id") or
            session.get("device") != hashed_device_uuid(config["uuid"])):
        raise LocoError("session_account_or_device_mismatch")
    token = session.get("token")
    if not isinstance(token, str) or not 20 <= len(token) <= 1024 or any(c.isspace() for c in token):
        raise LocoError("invalid_session_token")
    return session


def dial(host, port, identity):
    raw = socket.create_connection((host, port), timeout=5)
    try:
        # Routing servers provide IPs; verify the documented Kakao service identity.
        return ssl.create_default_context().wrap_socket(raw, server_hostname=identity)
    except BaseException:
        raw.close()
        raise


def receive_exact(stream, size, deadline):
    data = bytearray()
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("response_deadline")
        stream.settimeout(remaining)
        part = stream.recv(size - len(data))
        if not part:
            raise EOFError("connection_closed")
        data.extend(part)
    return bytes(data)


def request(stream, packet_id, method, body):
    encoded = BSON.encode(body)
    command = method.encode("ascii")
    if len(command) > 11 or len(encoded) > MAX_BODY:
        raise LocoError("outgoing_packet_too_large")
    stream.settimeout(8)
    stream.sendall(HEADER.pack(packet_id, 0, command.ljust(11, b"\0"), 0, len(encoded)) + encoded)
    deadline = time.monotonic() + 12
    for _ in range(100):
        response_id, status, name, body_type, size = HEADER.unpack(receive_exact(stream, 22, deadline))
        if body_type != 0 or not 5 <= size <= MAX_BODY:
            raise LocoError("invalid_packet_header")
        document = BSON(receive_exact(stream, size, deadline)).decode()
        name = name.rstrip(b"\0").decode("ascii")
        if name in ("KICKOUT", "KICKED"):
            raise LocoError("server_closed_session")
        if response_id == packet_id and name == method:
            code = document.get("status", status)
            if type(code) is not int or code != 0:
                suffix = str(code) if type(code) is int else "invalid"
                raise LocoError(method + "_status_" + suffix)
            return document
    raise LocoError("response_packet_limit")


class Session:
    def __init__(self, credentials):
        self.credentials = credentials
        self.stream = None
        self.next_id = 1

    def command(self, name, body):
        packet_id = self.next_id
        self.next_id += 1
        return request(self.stream, packet_id, name, body)

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None

    def connect(self):
        if LIVE_LOCO_DISABLED:
            raise LocoError(DISABLED_REASON)
        self._connect_profile("mac")

    def _connect_profile(self, device_os):
        credentials = self.credentials
        with dial("booking-loco.kakao.com", 443, "booking-loco.kakao.com") as stream:
            config = request(stream, 1, "GETCONF", {
                "MCCMNC": "99999", "os": device_os, "model": "", "userId": Int64(credentials["uid"]),
            })
        hosts = config.get("ticket", {}).get("lsl", [])
        ports = config.get("wifi", {}).get("ports", [5223])
        if not hosts or not ports:
            raise LocoError("no_checkin_route")
        checkin, identity = None, None
        for port in dict.fromkeys([443, int(ports[0])]):
            for name in ("kakao.com", "probe.kakao.com"):
                try:
                    with dial(hosts[0], port, name) as stream:
                        checkin = request(stream, 1, "CHECKIN", {
                            "userId": Int64(credentials["uid"]), "os": device_os, "ntype": 0,
                            "appVer": credentials["version"], "MCCMNC": "99999", "lang": "ko",
                            "countryISO": "KR", "useSub": True,
                        })
                    identity = name
                    break
                except (OSError, EOFError, LocoError):
                    continue
            if checkin is not None:
                break
        if checkin is None or not isinstance(checkin.get("host"), str):
            raise LocoError("checkin_failed")
        self.stream = dial(checkin["host"], int(checkin.get("port", 5223)), identity)
        login = self.command("LOGINLIST", {
            "appVer": credentials["version"], "prtVer": "1", "os": device_os, "lang": "ko",
            "duuid": credentials["device"], "oauthToken": credentials["token"], "dtype": 2, "ntype": 0,
            "MCCMNC": "99999", "revision": 0, "chatIds": [], "maxIds": [], "lastTokenId": Int64(0),
            "lbk": 0, "rp": Binary(b"\x00\x00\xff\xff\x00\x00"), "bg": False,
        })
        if login.get("userId") != credentials["uid"]:
            raise LocoError("authenticated_user_mismatch")


def send_self(text):
    """Send once to the unique local+server-verified self chat; never retry WRITE."""
    if LIVE_LOCO_DISABLED:
        return {"status": "disabled", "reason": DISABLED_REASON,
                "transport": "loco_tls", "messagesSent": 0}
    if not isinstance(text, str) or not text.strip() or "\0" in text or len(text.encode("utf-8")) > 10000:
        raise ValueError("text must contain 1–10000 UTF-8 bytes and no NUL")
    reader = Reader()
    rooms = [room for room in reader.all_rooms() if room["type"] == 5]
    if len(rooms) != 1:
        raise LocoError("self_room_not_unique")
    room_id = rooms[0]["chatId"]
    credentials = load_session()
    session = Session(credentials)
    try:
        session.connect()
        room = session.command("CHATONROOM", {"chatId": Int64(room_id)})
        kind = room.get("chatInfo", {}).get("type", room.get("t"))
        if kind != "MemoChat":
            raise LocoError("server_self_room_not_verified")
        try:
            result = session.command("WRITE", {"chatId": Int64(room_id), "msg": text, "type": 1, "noSeen": False})
        except Exception as error:
            rejected = isinstance(error, LocoError) and str(error).startswith("WRITE_status_")
            return {"status": "rejected" if rejected else "outcome_unknown", "transport": "loco_tls",
                    "retrySafe": False, "error": str(error) if isinstance(error, LocoError) else type(error).__name__}
        log_id = result.get("logId")
        verified, verification_error = False, None
        if type(log_id) in (int, Int64) and log_id > 0:
            try:
                history = session.command("SYNCMSG", {
                    "chatId": Int64(room_id), "cur": Int64(0), "cnt": 50, "max": Int64(log_id),
                })
                verified = any(
                    item.get("logId") == log_id and item.get("authorId") == credentials["uid"] and
                    item.get("message") == text
                    for item in (history.get("chatLogs") or []) if isinstance(item, dict)
                )
            except Exception as error:
                verification_error = str(error) if isinstance(error, LocoError) else type(error).__name__
        return {"status": "sent", "transport": "loco_tls", "destination": "self", "roomId": room_id,
                "logId": log_id, "text": text, "serverAcknowledged": True,
                "serverHistoryVerified": verified, "verificationError": verification_error, "retrySafe": False}
    finally:
        session.close()
