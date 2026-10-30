"""Read-only experiments below the GUI layer; no login or message commands.

By default only inspect the installed app. --network sends one anonymous GETCONF.
--task-access requests a task port, immediately releasing it without memory access.
"""

import argparse
import json
import plistlib
import socket
import ssl
import struct
import subprocess
import time
from pathlib import Path


APP = Path("/Applications/KakaoTalk.app")
HEADER = struct.Struct("<Ih11sBI")
MAX_BODY = 1024 * 1024
METHOD = b"GETCONF"


def booking_request():
    """The sole outgoing packet: public routing configuration for anonymous uid 0."""
    elements = bytearray()
    for key, value in (("MCCMNC", "99999"), ("os", "mac"), ("model", "")):
        encoded = value.encode() + b"\0"
        elements += b"\x02" + key.encode() + b"\0"
        elements += struct.pack("<i", len(encoded)) + encoded
    elements += b"\x12userId\0" + struct.pack("<q", 0)
    body = struct.pack("<i", len(elements) + 5) + elements + b"\0"
    return HEADER.pack(1, 0, METHOD.ljust(11, b"\0"), 0, len(body)) + body


def decode_bson(data):
    """Decode the bounded BSON subset used by GETCONF, rejecting malformed input."""
    def document(start, limit, depth):
        if depth > 16 or start + 5 > limit:
            raise ValueError("BSON document truncated or too deeply nested")
        length = struct.unpack_from("<i", data, start)[0]
        end = start + length
        if not 5 <= length <= MAX_BODY or end > limit or data[end - 1] != 0:
            raise ValueError("Invalid BSON document length or terminator")
        position = start + 4
        values = {}

        def take(count):
            nonlocal position
            if count < 0 or position + count > end - 1:
                raise ValueError("BSON element truncated")
            value = data[position:position + count]
            position += count
            return value

        while position < end - 1:
            kind = take(1)[0]
            zero = data.find(b"\0", position, min(end - 1, position + 257))
            if zero < 0:
                raise ValueError("Invalid BSON key")
            key = data[position:zero].decode("utf-8")
            if key in values:
                raise ValueError("Duplicate BSON key")
            position = zero + 1
            if kind == 0x02:
                size = struct.unpack("<i", take(4))[0]
                if size < 1:
                    raise ValueError("Invalid BSON string length")
                encoded = take(size)
                if encoded[-1] != 0:
                    raise ValueError("Invalid BSON string terminator")
                value = encoded[:-1].decode("utf-8")
            elif kind in (0x03, 0x04):
                value, position = document(position, end - 1, depth + 1)
                if kind == 0x04:
                    if list(value) != [str(i) for i in range(len(value))]:
                        raise ValueError("Invalid BSON array indices")
                    value = list(value.values())
            elif kind == 0x05:
                size = struct.unpack("<i", take(4))[0]
                subtype = take(1)[0]
                take(size)
                # Binary payloads are unnecessary here; retain only their size.
                value = {"binaryBytes": size, "subtype": subtype}
            elif kind == 0x08:
                value = take(1)[0]
                if value not in (0, 1):
                    raise ValueError("Invalid BSON boolean")
                value = bool(value)
            elif kind == 0x0A:
                value = None
            elif kind in (0x01, 0x09, 0x10, 0x12):
                fmt = {0x01: "<d", 0x09: "<q", 0x10: "<i", 0x12: "<q"}[kind]
                value = struct.unpack(fmt, take(struct.calcsize(fmt)))[0]
            else:
                raise ValueError("Unsupported BSON element type")
            values[key] = value
        return values, end

    result, end = document(0, len(data), 0)
    if end != len(data):
        raise ValueError("Unexpected bytes after BSON document")
    return result


def receive_exact(connection, size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("GETCONF deadline exceeded")
        connection.settimeout(remaining)
        chunk = connection.recv(size - len(result))
        if not chunk:
            raise EOFError("GETCONF connection closed before complete response")
        result.extend(chunk)
    return bytes(result)


def receive_booking(connection, deadline):
    packet_id, status, method, body_type, size = HEADER.unpack(
        receive_exact(connection, HEADER.size, deadline)
    )
    if packet_id != 1 or method.rstrip(b"\0") != METHOD or body_type != 0:
        raise ValueError("Unexpected GETCONF response header")
    if not 5 <= size <= MAX_BODY:
        raise ValueError("GETCONF response exceeds size bounds")
    body = receive_exact(connection, size, deadline)
    return status, body, decode_bson(body)


def probe_booking():
    context = ssl.create_default_context()
    host = "booking-loco.kakao.com"
    deadline = time.monotonic() + 15
    with socket.create_connection((host, 443), timeout=8) as connection:
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        with context.wrap_socket(connection, server_hostname=host) as tls:
            tls.sendall(booking_request())
            status, body, decoded = receive_booking(tls, deadline)
            body_status = decoded.get("status")
            if type(body_status) is not int:
                body_status = None
            return {
                "attempted": True,
                "endpoint": host + ":443",
                "command": "GETCONF",
                "anonymousUserId": 0,
                "tlsVersion": tls.version(),
                "certificateVerified": True,
                "headerStatus": status,
                "bodyStatus": body_status,
                "bodyBytes": len(body),
                "responseFields": sorted(decoded)[:20],
                "configurationReceived": status == 0 and body_status in (None, 0),
                "authenticated": False,
            }


def inspect_app():
    info_path = APP / "Contents/Info.plist"
    if not info_path.exists():
        return {"installed": False}
    with info_path.open("rb") as file:
        info = plistlib.load(file)
    binary = (APP / "Contents/MacOS/KakaoTalk").read_bytes()
    names = (
        "sendTextMessage:extra:supplement:",
        "isAvailableLeverageSendTextSchemeURLWithParameters:userInfo:",
        "leverageSendTextWithParameters:userInfo:",
        "isAcceptableExternalTalkSchemeURL:",
    )
    result = {
        "installed": True,
        "version": info.get("CFBundleShortVersionString"),
        "build": info.get("CFBundleVersion"),
        "appleScriptDeclared": bool(info.get("NSAppleScriptEnabled")),
        "scriptingDictionaryDeclared": bool(info.get("OSAScriptingDefinition")),
        "selectorStringsPresent": {name: name.encode() + b"\0" in binary for name in names},
    }
    signing = subprocess.run(
        ["codesign", "-d", "--entitlements", ":-", str(APP)],
        capture_output=True, timeout=10, check=True,
    )
    output = signing.stdout + signing.stderr
    start, end = output.find(b"<?xml"), output.find(b"</plist>")
    if start >= 0 and end > start:
        entitlements = plistlib.loads(output[start:end + len(b"</plist>")])
        result["debuggerEntitlement"] = bool(entitlements.get("com.apple.security.get-task-allow"))
        result["libraryValidationDisabled"] = bool(
            entitlements.get("com.apple.security.cs.disable-library-validation")
        )
        result["dyldEnvironmentAllowed"] = bool(
            entitlements.get("com.apple.security.cs.allow-dyld-environment-variables")
        )
    return result


def probe_task_access():
    result = subprocess.run(
        ["swift", str(Path(__file__).with_name("TaskAccessProbe.swift"))],
        capture_output=True, text=True, timeout=45, check=True,
    )
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", action="store_true", help="send one anonymous GETCONF over verified TLS")
    parser.add_argument("--task-access", action="store_true", help="request/release a task port without memory access")
    args = parser.parse_args()
    report = {"messagesSent": 0, "app": {}, "booking": {"attempted": False}, "taskAccess": {"attempted": False}}
    failed = False
    actions = [("app", inspect_app)]
    if args.network:
        actions.append(("booking", probe_booking))
    if args.task_access:
        actions.append(("taskAccess", probe_task_access))
    for name, action in actions:
        try:
            report[name] = action()
            if name == "booking" and not report[name]["configurationReceived"]:
                failed = True
        except (OSError, ValueError, EOFError, subprocess.SubprocessError) as error:
            failed = True
            report[name] = {"attempted": True, "errorType": type(error).__name__}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
