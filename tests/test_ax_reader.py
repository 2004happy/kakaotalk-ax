import copy
import unittest

from kakaotalk_automation import NativeAX


def text_row(index, text, time=None, date=None):
    parts = []
    if time:
        part = {"role": "AXStaticText", "AXValue": time}
        if date:
            part["AXHelp"] = date
        parts.append(part)
    parts.append({"role": "AXTextArea", "AXValue": text})
    return {"rowIndex": index, "parts": parts}


class SnapshotNative(NativeAX):
    def __init__(self):
        self.calls = []
        self.raw = {"status": "ok", "windowTitle": "Fixture", "processId": 123,
                    "rows": [text_row(0, "같은 말", "오전 11:09", "2026. 11. 2."),
                             text_row(1, "같은 말"), text_row(2, "오전 12:34"),
                             text_row(3, "줄 하나\n줄 둘 🙂"),
                             {"rowIndex": 4, "parts": [{"role": "AXImage", "AXDescription": "사진"}]}],
                    "exposedRowCount": 5, "rowStart": 0, "rowsTruncated": False, "elapsedMs": 80}

    def call(self, action, titles, text=None):
        self.calls.append((action, titles, text))
        return copy.deepcopy(self.raw)


class AXReadingTests(unittest.TestCase):
    def setUp(self):
        self.native = SnapshotNative()

    def test_read_keeps_multiline_emoji_and_distinct_repeated_messages(self):
        result = self.native.read_messages("Fixture")
        self.assertEqual([item["text"] for item in result["items"]],
                         ["같은 말", "같은 말", "오전 12:34", "줄 하나\n줄 둘 🙂", None])
        self.assertEqual(result["databaseCalls"], 0)
        self.assertEqual(result["uiActions"], 0)
        self.assertEqual(self.native.calls, [("read", ["Fixture"], None)])

    def test_missing_sender_or_grouped_time_is_not_invented(self):
        items = self.native.read_messages("Fixture")["items"]
        self.assertEqual((items[0]["timeText"], items[0]["dateText"]), ("오전 11:09", "2026. 11. 2."))
        self.assertIsNone(items[1]["timeText"])
        self.assertIsNone(items[1]["dateText"])
        self.assertIsNone(items[2]["timeText"])  # Time-shaped message text is still a body.
        self.assertTrue(all(item["sender"] is None for item in items))

    def test_time_combined_with_unread_count_is_read_without_inventing_a_sender(self):
        self.native.raw["rows"] = [text_row(0, "fixture", "1\n오후 2:48", "2026. 11. 3.")]
        item = self.native.read_messages("Fixture")["items"][0]
        self.assertEqual(item["timeText"], "오후 2:48")
        self.assertEqual(item["dateText"], "2026. 11. 3.")
        self.assertIsNone(item["sender"])

    def test_multiple_time_lines_are_not_assigned_an_arbitrary_time(self):
        self.native.raw["rows"] = [text_row(0, "fixture", "오전 10:00\n오후 2:48")]
        self.assertIsNone(self.native.read_messages("Fixture")["items"][0]["timeText"])

    def test_unknown_attachment_rows_are_preserved_and_coverage_is_explicit(self):
        result = self.native.read_messages("Fixture")
        self.assertEqual(result["items"][-1]["kind"], "unclassified")
        self.assertEqual(result["items"][-1]["axParts"][0]["AXDescription"], "사진")
        self.assertEqual(result["unclassifiedRowCount"], 1)
        self.assertFalse(result["coverage"]["historyComplete"])
        self.assertFalse(result["coverage"]["scrollPerformed"])
        self.native.raw.update(exposedRowCount=1005, rowStart=5, rowsTruncated=True)
        self.assertTrue(self.native.read_messages("Fixture")["coverage"]["rowsTruncated"])

    def test_older_pages_are_disjoint_chronological_and_snapshot_checked(self):
        first = self.native.read_messages("Fixture", 2)
        self.assertEqual([item["rowIndex"] for item in first["items"]], [3, 4])
        second = self.native.read_messages("Fixture", 2, first["nextOffset"], first["snapshotId"])
        self.assertEqual([item["rowIndex"] for item in second["items"]], [1, 2])
        third = self.native.read_messages("Fixture", 2, second["nextOffset"], first["snapshotId"])
        self.assertEqual([item["rowIndex"] for item in third["items"]], [0])
        self.assertFalse(third["hasMore"])
        self.assertIsNone(third["nextOffset"])

    def test_changed_snapshot_never_returns_a_shifted_page(self):
        first = self.native.read_messages("Fixture", 2)
        self.native.raw["rows"].append(text_row(5, "새 메시지"))
        self.native.raw["exposedRowCount"] += 1
        changed = self.native.read_messages("Fixture", 2, 2, first["snapshotId"])
        self.assertEqual(changed["status"], "snapshot_changed")
        self.assertEqual(changed["items"], [])

    def test_content_edits_also_invalidate_snapshot(self):
        first = self.native.read_messages("Fixture", 2)
        self.native.raw["rows"][0]["parts"][-1]["AXValue"] = "수정됨"
        self.assertEqual(self.native.read_messages("Fixture", 2, 2, first["snapshotId"])["status"], "snapshot_changed")

    def test_empty_transcript_is_a_partial_empty_snapshot(self):
        self.native.raw.update(rows=[], exposedRowCount=0)
        result = self.native.read_messages("Fixture")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertFalse(result["hasMore"])
        self.assertFalse(result["coverage"]["historyComplete"])

    def test_blocked_native_read_has_no_database_fallback(self):
        self.native.raw = {"status": "blocked", "reason": "target_room_window_ambiguous"}
        result = self.native.read_messages("Fixture")
        self.assertEqual(result, self.native.raw)
        self.assertEqual(len(self.native.calls), 1)

    def test_invalid_pagination_or_title_never_reaches_native(self):
        for request in [dict(window_title=""), dict(window_title=" " * 3), dict(window_title=True),
                        dict(window_title="Fixture", limit=True), dict(window_title="Fixture", limit=501),
                        dict(window_title="Fixture", offset=-1), dict(window_title="Fixture", offset=1),
                        dict(window_title="Fixture", snapshot_id="not-a-snapshot")]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.native.read_messages(**request)
        self.assertFalse(self.native.calls)


if __name__ == "__main__":
    unittest.main()
