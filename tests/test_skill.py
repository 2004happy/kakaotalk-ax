import base64
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import kakaotalk_mcp

SCRIPT = Path(__file__).resolve().parents[1] / "skills/kakaotalk/scripts/kakaotalk.py"
spec = importlib.util.spec_from_file_location("kakaotalk_skill_client", SCRIPT)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class SkillClientTests(unittest.TestCase):
    def test_ui_and_old_pc_sender_cannot_be_requested(self):
        for tool in client.BLOCKED_TOOLS:
            with self.subTest(tool=tool), self.assertRaises(ValueError):
                client.checked_arguments(tool, {})
        with self.assertRaises(ValueError):
            client.checked_arguments("kakaotalk_status", {"include_ui": True})
        with self.assertRaises(ValueError):
            client.checked_arguments("kakaotalk_status", [])

    def test_text_result_keeps_pagination_and_large_ids_exact(self):
        value = {"items": [{"logId": 2**60 + 123}], "hasMore": True, "nextOffset": 100}
        response = SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text=json.dumps(value))])
        self.assertEqual(client.render_result(response), value)

    def test_images_are_private_files_and_not_base64_output(self):
        encoded = base64.b64encode(b"fixture-image").decode()
        response = SimpleNamespace(isError=False, content=[
            SimpleNamespace(type="text", text='{"available":true}'),
            SimpleNamespace(type="image", data=encoded, mimeType="image/png"),
        ])
        with tempfile.TemporaryDirectory() as directory, patch.object(client, "IMAGE_CACHE", Path(directory)):
            result = client.render_result(response)
            image = Path(result["imageFiles"][0]["path"])
            self.assertTrue(image.is_absolute())
            self.assertEqual(image.read_bytes(), b"fixture-image")
            self.assertEqual(stat.S_IMODE(image.stat().st_mode), 0o600)
            self.assertNotIn(encoded, json.dumps(result))

    def test_write_error_is_uncertain_and_read_error_is_distinct(self):
        response = SimpleNamespace(isError=True, content=[SimpleNamespace(type="text", text="tool failed")])
        self.assertEqual(client.render_result(response)["status"], "mcp_error")
        result = client.render_result(response, write_attempt=True)
        self.assertEqual(result["status"], "outcome_unknown")
        self.assertFalse(result["retrySafe"])


class SkillInvocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_lost_send_response_is_not_retried(self):
        session = SimpleNamespace(call_tool=AsyncMock(side_effect=TimeoutError()))
        state = {"writeStarted": False}
        with self.assertRaises(TimeoutError):
            await client.call_once(session, "kakaotalk_send", {"room_id": 123, "text": "fixture"}, state)
        self.assertTrue(state["writeStarted"])
        session.call_tool.assert_awaited_once()

    async def test_blocked_tool_never_reaches_mcp(self):
        session = SimpleNamespace(call_tool=AsyncMock())
        with self.assertRaises(ValueError):
            await client.call_once(session, "kakaotalk_ui_read", {}, {})
        session.call_tool.assert_not_awaited()

    async def test_server_exposes_db_and_tablet_tools_without_ui_or_pc_sender(self):
        tools = await kakaotalk_mcp.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}
        self.assertFalse(set(by_name) & client.BLOCKED_TOOLS)
        self.assertIn("kakaotalk_rooms", by_name)
        self.assertIn("kakaotalk_send", by_name)
        self.assertFalse(by_name["kakaotalk_send"].annotations.readOnlyHint)
        self.assertFalse(by_name["kakaotalk_send"].annotations.idempotentHint)
        self.assertNotIn("include_ui", by_name["kakaotalk_status"].inputSchema.get("properties", {}))


if __name__ == "__main__":
    unittest.main()
