#!/usr/bin/env python3
"""
KakaoTalk Mac DB Reader - CLI tool to read KakaoTalk Mac's local encrypted database.

Requires: sqlcipher (brew install sqlcipher)
No Python packages needed beyond stdlib.
"""

import argparse
import base64
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from multiprocessing import Pool, TimeoutError as PoolTimeoutError, cpu_count

# ─── Constants ───────────────────────────────────────────────────────────────

KAKAO_BASE_PATH = os.path.expanduser(
    "~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Application Support/com.kakao.KakaoTalkMac/"
)
KAKAO_PLIST_PATH = os.path.expanduser(
    "~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Preferences/com.kakao.KakaoTalkMac.plist"
)
KAKAO_PLIST2_GLOB = os.path.expanduser(
    "~/Library/Containers/com.kakao.KakaoTalkMac/Data/Library/Preferences/com.kakao.KakaoTalkMac.*.plist"
)
CONFIG_PATH = os.path.expanduser("~/.config/kakaotalk-reader/config.json")
KST = timezone(timedelta(hours=9))
SQLCIPHER_BIN = shutil.which("sqlcipher") or "/opt/homebrew/opt/sqlcipher/bin/sqlcipher"


# ─── Crypto (from blluv's gist) ─────────────────────────────────────────────

def pbkdf2(password: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, salt, 100000, 128)


def hashed_device_uuid(uuid: str) -> str:
    b = uuid.encode("ascii")
    return base64.b64encode(hashlib.sha1(b).digest() + hashlib.sha256(b).digest()).decode("ascii")


def get_database_name(user_id: int, uuid: str) -> str:
    hawawa = ".".join([".", "F", str(user_id), "A", "F", "".join(reversed(uuid)), ".", "|"])
    salt = "".join(reversed(hashed_device_uuid(uuid))).encode("ascii")
    return pbkdf2(hawawa.encode("ascii"), salt).hex()[28:28 + 78]


def get_secure_key(user_id: int, uuid: str) -> str:
    h = hashed_device_uuid(uuid)
    hawawa = "F".join(["A", h, "|", "F", uuid[:5], "H", str(user_id), "|", uuid[7:]])
    return pbkdf2("".join(reversed(hawawa)).encode("ascii"), uuid[int(len(uuid) * 0.3):].encode("ascii")).hex()


# ─── System helpers ──────────────────────────────────────────────────────────

def get_hardware_uuid() -> str:
    try:
        r = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True, text=True, check=True)
        for line in r.stdout.splitlines():
            if "IOPlatformUUID" in line:
                return line.split('"')[-2]
    except Exception:
        pass
    return ""


def find_db_files() -> list[dict]:
    if not os.path.isdir(KAKAO_BASE_PATH):
        return []
    results = []
    for entry in os.scandir(KAKAO_BASE_PATH):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.name.endswith(("-shm", "-wal")):
            continue
        st = entry.stat()
        if st.st_size < 10000:
            continue
        results.append({
            "path": entry.path, "name": entry.name,
            "size": st.st_size, "has_wal": os.path.exists(entry.path + "-wal"),
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
        })
    results.sort(key=lambda x: (not x["has_wal"], -x["size"]))
    return results


def extract_user_id_from_plist() -> int | None:
    """Extract userId from plist by analyzing FSChatWindowTransparency keys.

    These keys follow the pattern: FSChatWindowTransparency<chatId><userId>
    The userId is the common suffix across all such keys.
    """
    import glob
    plist_files = [KAKAO_PLIST_PATH] + glob.glob(KAKAO_PLIST2_GLOB)

    for pf in plist_files:
        if not os.path.exists(pf):
            continue
        try:
            with open(pf, "rb") as f:
                plist = plistlib.load(f)
        except Exception:
            continue

        # Look for FSChatWindowTransparency keys
        fs_keys = [k for k in plist if k.startswith("FSChatWindowTransparency")]
        if len(fs_keys) >= 2:
            # Extract numeric suffix. The userId is the common ending.
            suffixes = [k.replace("FSChatWindowTransparency", "") for k in fs_keys]
            # Find longest common suffix
            if suffixes:
                rev = [s[::-1] for s in suffixes]
                common = ""
                for chars in zip(*rev):
                    if len(set(chars)) == 1:
                        common += chars[0]
                    else:
                        break
                common = common[::-1]
                if common.isdigit() and len(common) >= 4:
                    return int(common)

        # Also check AlertKakaoIDsList - less reliable but worth trying
    return None


# ─── Parallel brute-force ────────────────────────────────────────────────────

def extract_account_hashes_from_plist() -> set[bytes]:
    """Read SHA-512 account markers used by newer KakaoTalk preferences."""
    import glob
    hashes = set()
    prefixes = ("DESIGNATEDFRIENDSREVISION:", "DENYFILEEXTIONSIONREVISION:")
    for path in [KAKAO_PLIST_PATH] + glob.glob(KAKAO_PLIST2_GLOB):
        try:
            with open(path, "rb") as f:
                plist = plistlib.load(f)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        for name in plist:
            if isinstance(name, str) and name.startswith(prefixes):
                suffix = name.split(":", 1)[1]
                if re.fullmatch(r"[0-9a-fA-F]{128}", suffix):
                    hashes.add(bytes.fromhex(suffix))
    hashes.difference_update(hashlib.sha512(value).digest() for value in (b"", b"0", b"-1"))
    return hashes


def _check_hash_range(args):
    start, end, uuid, target, hashes = args
    for uid in range(start, end):
        if hashlib.sha512(str(uid).encode("ascii")).digest() in hashes:
            if get_database_name(uid, uuid) == target:
                return uid
    return None


def _check_range(args):
    start, end, uuid, target = args
    for uid in range(start, end):
        if get_database_name(uid, uuid) == target:
            return uid
    return None


def bruteforce_user_id(uuid: str, target: str, max_id: int | None = None,
                      timeout: float = 60) -> int | None:
    hashes = extract_account_hashes_from_plist()
    if max_id is None:
        max_id = 6_000_000_000 if hashes else 2_000_000
    if max_id < 1 or timeout <= 0:
        raise ValueError("max_id and timeout must be positive")
    cores = min(cpu_count() or 4, 4)
    chunk = 250_000 if hashes else max(1000, max_id // (cores * 4))
    ranges = [(s, min(s + chunk, max_id + 1), uuid, target) + ((hashes,) if hashes else ())
              for s in range(1, max_id + 1, chunk)]
    checker = _check_hash_range if hashes else _check_range
    method = "account SHA-512" if hashes else "database filename"
    print(f"Searching userId 1..{max_id} via {method} ({cores} cores, {timeout:g}s limit)...",
          file=sys.stderr)

    with Pool(cores) as pool:
        deadline = time.monotonic() + timeout
        results = pool.imap_unordered(checker, ranges)
        for i in range(len(ranges)):
            try:
                result = results.next(timeout=max(0, deadline - time.monotonic()))
            except PoolTimeoutError:
                pool.terminate()
                print("userId search timed out; retry setup with a larger --timeout or --user-id.",
                      file=sys.stderr)
                return None
            if result is not None:
                pool.terminate()
                print(f"  Found userId: {result}", file=sys.stderr)
                return result
            if (i + 1) % (cores * 2) == 0:
                print(f"  ...checked ~{min((i + 1) * chunk, max_id):,}", file=sys.stderr)
    return None


# ─── SQLCipher ───────────────────────────────────────────────────────────────

def validate_read_sql(sql: str):
    if not isinstance(sql, str) or len(sql) > 1_000_000:
        raise ValueError("SQL must be a string of at most 1 MB")
    # Ignore quoted values/identifiers and comments while checking SQL syntax.
    stripped = re.sub(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]", " ", sql)
    if re.search(r"(?m)^\s*\.", stripped):
        raise ValueError("SQLite shell commands are not supported")
    if ";" in stripped.strip().rstrip(";"):
        raise ValueError("Use one read-only SQL statement per call")
    if not re.match(r"\s*(SELECT|WITH|EXPLAIN|PRAGMA)\b", stripped, re.I):
        raise ValueError("Only read-only SELECT, WITH, EXPLAIN, and inspection PRAGMA queries are supported")
    if re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER|ATTACH|DETACH|VACUUM|REINDEX|ANALYZE|load_extension|writefile|readfile)\b", stripped, re.I):
        raise ValueError("Only read-only SQL without filesystem or extension functions is supported")
    pragma = re.match(r"\s*PRAGMA\s+(\w+)", stripped, re.I)
    if pragma and pragma[1].lower() not in {
        "table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list",
        "compile_options", "database_list", "function_list", "collation_list", "module_list",
        "integrity_check", "quick_check", "cipher_integrity_check",
    }:
        raise ValueError("This PRAGMA is not a read-only inspection query")


def sqlcipher_query(db_path: str, key: str, sql: str, json_mode: bool = True) -> str:
    validate_read_sql(sql)
    if not os.path.exists(SQLCIPHER_BIN):
        print(f"Error: sqlcipher not found. Install: brew install sqlcipher", file=sys.stderr)
        sys.exit(1)

    cmds = [
        "PRAGMA cipher_default_compatibility = 3;",
        f"PRAGMA KEY='{key}';",
        ".mode json" if json_mode else ".mode column\n.headers on",
        sql,
    ]
    r = subprocess.run([SQLCIPHER_BIN, "-noinit", "-batch", "-bail", "-safe", "-readonly", db_path], input="\n".join(cmds) + "\n",
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        err = r.stderr.strip()
        if "file is not a database" in err or "file is encrypted" in err:
            raise RuntimeError(f"Decryption failed: {err}")
        if err:
            raise RuntimeError(f"sqlcipher error: {err}")
    return r.stdout.strip()


def query_json(db_path: str, key: str, sql: str) -> list[dict]:
    raw = sqlcipher_query(db_path, key, sql)
    lines = raw.splitlines()
    while lines and lines[0].strip() in ("", "ok"):
        lines.pop(0)
    raw = "\n".join(lines).strip()
    if not raw:
        return []
    if raw.startswith(("Error", "Parse error", "Runtime error")):
        raise RuntimeError(f"Query failed: {raw[:200]}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        idx = raw.find("[")
        if idx >= 0:
            return json.loads(raw[idx:])
        raise RuntimeError(f"Parse error: {raw[:200]}")


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    directory = os.path.dirname(CONFIG_PATH)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as f:
            temporary = f.name
            os.fchmod(f.fileno(), 0o600)
            json.dump(cfg, f, indent=2)
        os.replace(temporary, CONFIG_PATH)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def resolve(args) -> tuple[str, str]:
    """Resolve db_path and secure_key."""
    cfg = load_config()
    uuid = getattr(args, "uuid", None) or cfg.get("uuid") or get_hardware_uuid()
    user_id = getattr(args, "user_id", None) or cfg.get("user_id")
    db_path = getattr(args, "db_path", None) or cfg.get("db_path")

    if not uuid:
        print("Error: hardware UUID not found.", file=sys.stderr)
        sys.exit(1)

    if not db_path:
        dbs = find_db_files()
        if not dbs:
            print("Error: no KakaoTalk DB files found.", file=sys.stderr)
            sys.exit(1)
        db_path = dbs[0]["path"]
        print(f"DB: {os.path.basename(db_path)}", file=sys.stderr)

    if not user_id:
        # Try extracting from plist first (instant)
        user_id = extract_user_id_from_plist()
        if user_id:
            # Verify it matches the DB name
            if get_database_name(user_id, uuid) == os.path.basename(db_path):
                print(f"userId from plist: {user_id}", file=sys.stderr)
            else:
                user_id = None

        if not user_id:
            user_id = bruteforce_user_id(uuid, os.path.basename(db_path))
            if user_id is None:
                print("Error: userId not found.", file=sys.stderr)
                sys.exit(1)

    updated = {**cfg, "uuid": uuid, "user_id": user_id, "db_path": db_path}
    if cfg != updated:
        save_config(updated)
    return db_path, get_secure_key(user_id, uuid)


def fmt_ts(ts) -> str:
    if not ts:
        return ""
    try:
        val = int(ts)
        if val == 0:
            return ""
        dt = datetime.fromtimestamp(val if val < 1e12 else val / 1000, tz=KST)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ts)


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_info(args):
    uuid = get_hardware_uuid()
    cfg = load_config()
    dbs = find_db_files()
    plist_uid = extract_user_id_from_plist()

    if args.format == "json":
        print(json.dumps({"hardware_uuid": uuid, "plist_user_id": plist_uid,
                          "config": cfg, "db_files": dbs}, indent=2, ensure_ascii=False))
    else:
        print(f"Hardware UUID: {uuid}")
        print(f"sqlcipher: {SQLCIPHER_BIN} ({'OK' if os.path.exists(SQLCIPHER_BIN) else 'NOT FOUND'})")
        print(f"userId (plist): {plist_uid or 'not found'}")
        print(f"Config: {CONFIG_PATH}")
        if cfg:
            print(f"  user_id: {cfg.get('user_id')}")
            print(f"  db: {os.path.basename(cfg.get('db_path', ''))}")
        print(f"\nDB files ({len(dbs)}):")
        for d in dbs:
            tag = " [ACTIVE]" if d["has_wal"] else ""
            print(f"  {d['name'][:50]}  {d['size']:>10,}B{tag}")


def cmd_setup(args):
    uuid = getattr(args, "uuid", None) or get_hardware_uuid()
    if not uuid:
        print("Error: no hardware UUID.", file=sys.stderr); sys.exit(1)
    print(f"UUID: {uuid}")

    dbs = find_db_files()
    if not dbs:
        print("Error: no DB files.", file=sys.stderr); sys.exit(1)

    db_path = getattr(args, "db_path", None) or dbs[0]["path"]
    target = os.path.basename(db_path)
    print(f"DB: {target}")

    user_id = getattr(args, "user_id", None)
    if not user_id:
        user_id = extract_user_id_from_plist()
        if user_id and get_database_name(user_id, uuid) == target:
            print(f"userId (from plist): {user_id}")
        else:
            user_id = None
    if not user_id:
        user_id = bruteforce_user_id(uuid, target, args.max_id, args.timeout)
        if not user_id:
            print("Error: userId not found.", file=sys.stderr); sys.exit(1)

    print(f"User ID: {user_id}")
    key = get_secure_key(user_id, uuid)
    try:
        rows = query_json(db_path, key, "SELECT count(*) as cnt FROM sqlite_master;")
        print(f"Decryption OK! ({rows[0]['cnt']} objects)")
    except Exception as e:
        print(f"Decryption failed: {e}", file=sys.stderr); sys.exit(1)

    save_config({"uuid": uuid, "user_id": user_id, "db_path": db_path})
    print(f"Saved to {CONFIG_PATH}")


def cmd_tables(args):
    p, k = resolve(args)
    rows = query_json(p, k, "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name;")
    if args.format == "json":
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"  {r['type']:6s} {r['name']}")


def cmd_schema(args):
    p, k = resolve(args)
    if args.table:
        rows = query_json(p, k, f"PRAGMA table_info({args.table});")
        if args.format == "json":
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"  {r.get('name',''):30s} {r.get('type','')}")
    else:
        rows = query_json(p, k, "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name;")
        if args.format == "json":
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            for r in rows:
                print(f"\n── {r['name']} ──\n  {r.get('sql','')}")


def _resolve_room_name(p: str, k: str, row: dict) -> str:
    from kakaotalk_service import Reader
    return Reader(p, k).find_room(int(row["chatId"]))["displayName"]


def cmd_rooms(args):
    page = _reader_for(args).rooms(args.limit, args.offset, args.query, args.room_type, args.unread_only)
    if args.format == "json":
        print(json.dumps(page, ensure_ascii=False, indent=2))
    else:
        for room in page["items"]:
            print(f"[{room['roomType']}] {room['chatId']} | {room['displayName']} ({room['activeMembersCount']}명, 안 읽음 {room['countOfNewMessage']})")
        print(f"{len(page['items'])}/{page['total']} rooms; next offset: {page['nextOffset']}")


def cmd_read(args):
    page = _reader_for(args).messages(room_id=args.room_id or 0, room_name=args.room_name, limit=args.limit,
                             offset=args.offset, since=args.since, until=args.until,
                             before_log_id=args.before_log_id, after_log_id=args.after_log_id)
    _print_page(args, page)


def cmd_search(args):
    if not args.keyword:
        raise ValueError("keyword must not be empty")
    page = _reader_for(args).messages(keyword=args.keyword, room_id=args.room_id, room_name=args.room_name,
                             limit=args.limit, offset=args.offset, since=args.since, until=args.until)
    _print_page(args, page)


def _parse_date(s: str) -> datetime | None:
    """Parse date string like '2026-03-18', 'today', 'yesterday', '-3d'."""
    if not s:
        return None
    s = s.strip().lower()
    now = datetime.now(tz=KST)
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if s == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if s.startswith("-") and s.endswith("d"):
        try:
            days = int(s[1:-1])
            return (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            pass
    try:
        dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=KST)
        return dt
    except ValueError:
        pass
    print(f"Invalid date: {s} (use YYYY-MM-DD, today, yesterday, -3d)", file=sys.stderr)
    sys.exit(1)


def cmd_recent(args):
    page = _reader_for(args).messages(limit=args.limit, offset=args.offset, since=args.since, until=args.until,
                             before_log_id=args.before_log_id, after_log_id=args.after_log_id)
    _print_page(args, page)


def cmd_users(args):
    page = _reader_for(args).users(args.query, args.limit, args.offset)
    print(json.dumps(page, ensure_ascii=False, indent=2))


def cmd_sql(args):
    p, k = resolve(args)
    try:
        rows = query_json(p, k, args.query)
        if args.format == "json":
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            if rows and isinstance(rows[0], dict):
                cols = list(rows[0].keys())
                print(" | ".join(cols))
                print("-" * min(120, len(" | ".join(cols))))
                for r in rows:
                    print(" | ".join(str(r.get(c, ""))[:80] for c in cols))
    except Exception as e:
        print(f"SQL error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_find_userid(args):
    uuid = getattr(args, "uuid", None) or get_hardware_uuid()
    if not uuid:
        print("Error: no hardware UUID.", file=sys.stderr); sys.exit(1)

    db_path = getattr(args, "db_path", None)
    if not db_path:
        dbs = find_db_files()
        if not dbs:
            print("No DB files.", file=sys.stderr); sys.exit(1)
        db_path = dbs[0]["path"]

    target = os.path.basename(db_path)
    print(f"UUID: {uuid}", file=sys.stderr)
    print(f"Target: {target}", file=sys.stderr)

    # Try plist first
    plist_uid = extract_user_id_from_plist()
    if plist_uid and get_database_name(plist_uid, uuid) == target:
        print(f"Found from plist! userId = {plist_uid}")
        return

    uid = bruteforce_user_id(uuid, target, args.max_id, args.timeout)
    if uid:
        print(json.dumps({"user_id": uid}) if args.format == "json" else f"Found! userId = {uid}")
    else:
        print("userId not found within the search limits.", file=sys.stderr); sys.exit(1)


def cmd_export(args):
    """Export decrypted DB as a plain SQLite file."""
    p, k = resolve(args)
    out = args.output or os.path.join(os.getcwd(), "kakaotalk.db")

    if os.path.exists(out) and not args.force:
        print(f"Error: {out} already exists. Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    if os.path.exists(out):
        os.remove(out)

    # Use sqlcipher's ATTACH + sqlcipher_export to produce a plain SQLite DB
    sql = f"""
PRAGMA cipher_default_compatibility = 3;
PRAGMA KEY='{k}';
ATTACH DATABASE '{out}' AS plaintext KEY '';
SELECT sqlcipher_export('plaintext');
DETACH DATABASE plaintext;
"""
    r = subprocess.run([SQLCIPHER_BIN, p], input=sql,
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not os.path.exists(out):
        print(f"Export failed: {r.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    size = os.path.getsize(out)
    print(f"Exported: {out} ({size:,} bytes)")
    print(f"\nCowork에서 사용하려면:")
    print(f"  1. 이 파일이 있는 폴더를 Cowork에 마운트")
    print(f"  2. sqlite3 kakaotalk.db 로 쿼리")
    print(f"\n주요 테이블:")
    print(f"  NTChatRoom    — chatId, type, chatName, linkId, lastLogId")
    print(f"  NTChatMessage — chatId, logId, authorId, type, sentAt, message, attachment")
    print(f"  NTUser        — userId, linkId, nickName")


# ─── Message display ────────────────────────────────────────────────────────

MSG_TYPES = {
    1: "", 2: "[photo]", 3: "[video]", 4: "[audio]",
    5: "[contact]", 6: "[file]", 12: "[sticker]",
    14: "[map]", 16: "[schedule]", 18: "[voip]",
    22: "[emoji]", 23: "[shout]", 24: "[reply]",
    26: "[reply]", 27: "[album]", 51: "[media]",
    71: "[card]", 72: "[notification card]",
}


def _print_msgs(rows: list[dict], show_room: bool = False):
    for m in rows:
        ts = fmt_ts(m.get("sentAt"))
        nick = m.get("nickName") or str(m.get("authorId", "?"))
        text = m.get("text") or m.get("message") or ""
        mtype = int(m.get("type", 1))
        tag = MSG_TYPES.get(mtype, f"[type:{mtype}]") if mtype != 1 else ""

        room = f"[{m.get('roomName') or m['chatId']}] " if show_room and "chatId" in m else ""

        # For non-text types, try to show attachment info
        if not text and tag and m.get("attachment"):
            try:
                att = json.loads(m["attachment"])
                text = att.get("url") or att.get("name") or att.get("k") or str(att)[:100]
            except (json.JSONDecodeError, TypeError):
                text = str(m["attachment"])[:100]

        line = f"[{ts}] {room}{nick}: {text}" + (f" {tag}" if tag else "")
        print(line)


# ─── Main ────────────────────────────────────────────────────────────────────

def _reader_for(args):
    from kakaotalk_service import Reader
    path, key = resolve(args)
    return Reader(path, key, getattr(args, "user_id", None) or load_config().get("user_id"))


def _print_page(args, page):
    if args.format == "json":
        print(json.dumps(page, ensure_ascii=False, indent=2))
    else:
        print(f"Messages: {len(page['items'])}/{page['total']}; next offset: {page['nextOffset']}\n")
        _print_msgs(page["items"], show_room=True)


def cmd_extra(args):
    from kakaotalk_service import media_entries, visible_media
    if args.command == "ui":
        from kakaotalk_ui import snapshot
        result = snapshot(args.window, status_only=args.status)
    else:
        reader = _reader_for(args)
        if args.command == "status":
            result = reader.status()
        elif args.command == "room":
            result = reader.room(args.room_id, args.room_name)
        elif args.command == "message":
            result = reader.message(args.room_id, args.log_id)
        elif args.command == "activity":
            result = reader.activity(args.since, args.until)
        elif args.command == "notices":
            result = reader.notices(args.room_id, args.limit, args.offset)
        elif args.command == "bookmarks":
            result = reader.bookmarks(args.room_id, args.limit, args.offset)
        elif args.command == "folders":
            result = reader.folders()
        elif args.command == "events":
            result = reader.events(args.since, args.until, args.room_id, args.limit, args.offset)
        elif args.command == "media":
            if args.download:
                from kakaotalk_media import retrieve
                result, _ = retrieve(reader, args.room_id, args.log_id, args.index)
            else:
                result = {"items": visible_media(media_entries(reader.raw_message(args.room_id, args.log_id)))}
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(prog="kakaotalk-reader", description="Read KakaoTalk local data and app surfaces without sending messages.")
    parser.add_argument("--uuid", help="Hardware UUID override")
    parser.add_argument("--user-id", type=int, help="KakaoTalk numeric user ID override")
    parser.add_argument("--db-path", help="Local database override")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    sub = parser.add_subparsers(dest="command")

    def paging(p, limit=100):
        p.add_argument("-n", "--limit", type=int, default=limit)
        p.add_argument("--offset", type=int, default=0)

    def dates(p, since="", until=""):
        p.add_argument("--since", default=since)
        p.add_argument("--until", default=until)

    def room_filter(p):
        p.add_argument("--room-id", type=int, default=0)
        p.add_argument("--room-name", default="")

    def cursors(p):
        p.add_argument("--before-log-id", type=int, default=0)
        p.add_argument("--after-log-id", type=int, default=0)

    sub.add_parser("info", help="Inspect setup")
    for name in ("setup", "find-userid"):
        p = sub.add_parser(name, help="Detect and verify the local account")
        p.add_argument("--max-id", type=int, help="Search ceiling: 6 billion with account hashes, otherwise 2 million")
        p.add_argument("--timeout", type=float, default=60)
    sub.add_parser("tables", help="List database tables")
    p = sub.add_parser("schema", help="Read table schema")
    p.add_argument("table", nargs="?")

    p = sub.add_parser("rooms", help="List/search rooms with exact names and unread counts")
    paging(p)
    p.add_argument("--query", default="")
    p.add_argument("--room-type", type=int, default=-1)
    p.add_argument("--unread-only", action="store_true")
    p = sub.add_parser("read", help="Read messages from a room ID or --room-name")
    p.add_argument("room_id", nargs="?", type=int)
    p.add_argument("--room-name", default="")
    paging(p, 50); dates(p); cursors(p)
    p = sub.add_parser("search", help="Search full message/card/reply text")
    p.add_argument("keyword")
    paging(p, 50); dates(p); room_filter(p)
    p = sub.add_parser("recent", help="Read recent messages across rooms")
    paging(p, 30); dates(p); cursors(p)
    p = sub.add_parser("users", help="List/search contact names")
    paging(p)
    p.add_argument("--query", default="")
    p = sub.add_parser("sql", help="Run one read-only inspection query")
    p.add_argument("query")

    sub.add_parser("status", help="Show capabilities and local history coverage")
    p = sub.add_parser("room", help="Read room details, participants, and notices")
    room_filter(p)
    p = sub.add_parser("message", help="Read one complete message")
    p.add_argument("room_id", type=int)
    p.add_argument("log_id", type=int)
    p = sub.add_parser("activity", help="Summarize message activity by room")
    dates(p, "last7d", "now")
    for name in ("notices", "bookmarks", "events"):
        p = sub.add_parser(name, help=f"Read local {name}")
        p.add_argument("--room-id", type=int, default=0)
        paging(p)
        if name == "events": dates(p, "today")
    sub.add_parser("folders", help="Read chat folders")
    p = sub.add_parser("media", help="List or explicitly download message media")
    p.add_argument("room_id", type=int)
    p.add_argument("log_id", type=int)
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--download", action="store_true")
    p = sub.add_parser("ui", help="Read current KakaoTalk app surface")
    p.add_argument("--window", default="")
    p.add_argument("--status", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    commands = {"info": cmd_info, "setup": cmd_setup, "find-userid": cmd_find_userid,
                "tables": cmd_tables, "schema": cmd_schema, "rooms": cmd_rooms,
                "read": cmd_read, "search": cmd_search, "recent": cmd_recent,
                "users": cmd_users, "sql": cmd_sql}
    try:
        commands.get(args.command, cmd_extra)(args)
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)



if __name__ == "__main__":
    main()
