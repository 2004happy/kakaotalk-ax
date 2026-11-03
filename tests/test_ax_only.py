"""The released entry points cannot import or invoke the removed DB implementation."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REMOVED = ("kakaotalk_reader", "kakaotalk_service", "kakaotalk_media", "kakaotalk_loco",
           "kakaotalk_tablet", "kakaotalk_ui")


class AXOnlySurfaceTests(unittest.TestCase):
    def test_importing_public_api_never_loads_database_dependencies(self):
        code = '''
import importlib.abc, json, sys
blocked = {"sqlite3", "_sqlite3", "sqlcipher3", "pysqlcipher3",
           "kakaotalk_reader", "kakaotalk_service", "kakaotalk_media"}
class NoDatabase(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked:
            raise AssertionError("Database dependency requested: " + fullname)
sys.meta_path.insert(0, NoDatabase())
import kakaotalk_automation, kakaotalk_ax, kakaotalk_state, kakaotalk_mcp
print(json.dumps(sorted(blocked.intersection(sys.modules))))
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_removed_implementations_are_not_shipped_as_source(self):
        for module in REMOVED:
            with self.subTest(module=module):
                self.assertFalse((ROOT / "src" / (module + ".py")).exists())

    def test_obsolete_cli_routes_are_rejected_before_native_access(self):
        for command in ("setup", "sql", "export", "send", "prepare", "result"):
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, "-m", "kakaotalk_automation", command],
                                        cwd=ROOT, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid choice", result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
