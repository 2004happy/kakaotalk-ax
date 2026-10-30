#!/usr/bin/env python3
"""Invoke the local KakaoTalk MCP server without a persistent connector registration."""

import argparse
import asyncio
import base64
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path


DEFAULT_ROOT = Path.home() / "Documents/workspace/kakaotalk-reader"
IMAGE_CACHE = Path.home() / ".cache/kakaotalk-reader/skill-images"
BLOCKED_TOOLS = {"kakaotalk_send_self", "kakaotalk_ui_status", "kakaotalk_ui_read"}


def checked_arguments(name, arguments):
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    if name in BLOCKED_TOOLS or (name == "kakaotalk_status" and arguments.get("include_ui")):
        raise ValueError("UI automation and the old PC-session sender are excluded")
    return arguments


def render_result(result, write_attempt=False):
    texts, images = [], []
    for part in result.content:
        if part.type == "text":
            try:
                texts.append(json.loads(part.text))
            except ValueError:
                texts.append(part.text)
        elif part.type == "image":
            if len(part.data) > 16 * 1024 * 1024:
                raise ValueError("Image result exceeds size limit")
            data = base64.b64decode(part.data, validate=True)
            IMAGE_CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
            extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                         "image/gif": ".gif"}.get(part.mimeType, ".bin")
            descriptor, filename = tempfile.mkstemp(prefix="kakao-", suffix=extension, dir=IMAGE_CACHE)
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
            images.append({"path": str(Path(filename).absolute()), "mimeType": part.mimeType})
    value = texts[0] if len(texts) == 1 else {"content": texts}
    if images:
        value = {**value, "imageFiles": images} if isinstance(value, dict) else {"content": value, "imageFiles": images}
    if result.isError:
        return {"status": "outcome_unknown" if write_attempt else "mcp_error",
                "retrySafe": False if write_attempt else True, "detail": value}
    return value


async def call_once(session, name, arguments, state):
    checked_arguments(name, arguments)
    state["writeStarted"] = name == "kakaotalk_send"
    result = await session.call_tool(name, arguments)
    return render_result(result, state["writeStarted"])


async def invoke(root, name, arguments, timeout, state):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=str(root / ".venv/bin/python"), args=["-m", "kakaotalk_mcp"])
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)) as session:
            await session.initialize()
            available = await session.list_tools()
            tools = {tool.name: tool for tool in available.tools if tool.name not in BLOCKED_TOOLS}
            if name is None:
                return {"tools": [tool.model_dump(mode="json", exclude_none=True) for tool in tools.values()]}
            if name not in tools:
                raise ValueError("Requested tool is not exposed by this server")
            return await call_once(session, name, arguments, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("KAKAOTALK_READER_ROOT", DEFAULT_ROOT)))
    parser.add_argument("--timeout", type=int, default=75)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("tools")
    call = commands.add_parser("call")
    call.add_argument("tool")
    call.add_argument("--args-file", type=Path)
    send = commands.add_parser("send")
    send.add_argument("--room-id", type=int, required=True)
    send.add_argument("--text-file", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.expanduser().absolute()
    python = root / ".venv/bin/python"
    if not python.is_file():
        print(json.dumps({"status": "setup_required", "projectRoot": str(root), "detail": "Project .venv is missing"}))
        return 1
    if Path(sys.prefix).resolve() != (root / ".venv").resolve():
        os.execv(str(python), [str(python), str(Path(__file__).absolute()), *sys.argv[1:]])
    state = {"writeStarted": False}
    try:
        if not 1 <= args.timeout <= 300:
            raise ValueError("timeout must be between 1 and 300 seconds")
        if args.command == "status":
            name, arguments = "kakaotalk_status", {}
        elif args.command == "tools":
            name, arguments = None, {}
        elif args.command == "send":
            text = sys.stdin.read() if str(args.text_file) == "-" else args.text_file.read_text(encoding="utf-8")
            name, arguments = "kakaotalk_send", {"room_id": args.room_id, "text": text}
        else:
            name = args.tool
            arguments = json.loads(args.args_file.read_text(encoding="utf-8")) if args.args_file else {}
        checked_arguments(name, arguments)
        value = asyncio.run(invoke(root, name, arguments, args.timeout, state))
        print(json.dumps(value, ensure_ascii=False, indent=2))
        errors = {"outcome_unknown", "mcp_error", "tablet_login_required", "tablet_verification_required", "install_send_extra"}
        return int(isinstance(value, dict) and value.get("status") in errors)
    except (Exception, KeyboardInterrupt) as error:
        value = {"status": "outcome_unknown" if state["writeStarted"] else "failed",
                 "errorType": type(error).__name__, "retrySafe": not state["writeStarted"]}
        print(json.dumps(value, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
