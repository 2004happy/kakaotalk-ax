import hashlib
import json
import os
import plistlib
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kakaotalk_reader as reader


UUID = "00000000-0000-0000-0000-000000000000"


class AccountRecoveryTests(unittest.TestCase):
    def test_reads_both_markers_and_skips_invalid_and_empty_values(self):
        first = hashlib.sha512(b"42").digest()
        second = hashlib.sha512(b"43").digest()
        values = {
            "DESIGNATEDFRIENDSREVISION:" + first.hex(): 1,
            "DENYFILEEXTIONSIONREVISION:" + second.hex().upper(): 1,
            "DENYFILEEXTIONSIONREVISION:" + hashlib.sha512(b"0").hexdigest(): 0,
            "DENYFILEEXTIONSIONREVISION:not-a-hash": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.plist"
            path.write_bytes(plistlib.dumps(values))
            with patch.object(reader, "KAKAO_PLIST_PATH", str(path)), \
                    patch.object(reader, "KAKAO_PLIST2_GLOB", str(Path(directory) / "missing*")):
                self.assertEqual(reader.extract_account_hashes_from_plist(), {first, second})

    def test_ignores_stale_account_until_database_filename_matches(self):
        hashes = {hashlib.sha512(str(uid).encode()).digest() for uid in (42, 43)}
        target = reader.get_database_name(43, UUID)
        self.assertEqual(reader._check_hash_range((42, 44, UUID, target, hashes)), 43)

    def test_default_hash_search_can_exceed_legacy_two_million_limit(self):
        uid = 2_000_123
        target = reader.get_database_name(uid, UUID)
        hashes = {hashlib.sha512(str(uid).encode()).digest()}
        with patch.object(reader, "extract_account_hashes_from_plist", return_value=hashes):
            self.assertEqual(reader.bruteforce_user_id(UUID, target, timeout=10), uid)

    def test_search_stops_at_deadline(self):
        hashes = {hashlib.sha512(b"2000000000").digest()}
        with patch.object(reader, "extract_account_hashes_from_plist", return_value=hashes):
            self.assertIsNone(reader.bruteforce_user_id(UUID, "0" * 78,
                                                       max_id=1_000_000_000, timeout=0.01))

    def test_existing_config_becomes_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{}")
            path.chmod(0o644)
            with patch.object(reader, "CONFIG_PATH", str(path)):
                reader.save_config({"user_id": 42})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text()), {"user_id": 42})


@unittest.skipUnless(os.path.isfile(reader.SQLCIPHER_BIN), "sqlcipher is required")
class ReadOnlyDatabaseTests(unittest.TestCase):
    def test_encrypted_database_allows_reads_and_rejects_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "fixture.db")
            key = reader.get_secure_key(42, UUID)
            result = subprocess.run(
                [reader.SQLCIPHER_BIN, path],
                input=f"PRAGMA cipher_default_compatibility=3;\nPRAGMA key='{key}';\n"
                      "CREATE TABLE fixture(value INTEGER);\nINSERT INTO fixture VALUES(1);\n",
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(reader.query_json(path, key, "SELECT value FROM fixture;"), [{"value": 1}])
            with self.assertRaisesRegex(ValueError, "read.?only"):
                reader.query_json(path, key, "INSERT INTO fixture VALUES(2);")
            with patch.object(reader, "validate_read_sql"):
                with self.assertRaisesRegex(RuntimeError, "read.?only"):
                    reader.query_json(path, key, "INSERT INTO fixture VALUES(2);")
            self.assertEqual(reader.query_json(path, key, "SELECT count(*) AS n FROM fixture;"), [{"n": 1}])
            self.assertEqual(reader.query_json(path, key, "SELECT value FROM fixture WHERE value=999;"), [])


if __name__ == "__main__":
    unittest.main()
