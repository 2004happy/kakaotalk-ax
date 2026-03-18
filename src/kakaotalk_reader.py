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
from datetime import datetime, timezone, timedelta
from multiprocessing import Pool, cpu_count

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

def _check_range(args):
    start, end, uuid, target = args
    for uid in range(start, end):
        if get_database_name(uid, uuid) == target:
            return uid
    return None


def bruteforce_user_id(uuid: str, target: str, max_id: int = 2_000_000) -> int | None:
    cores = cpu_count() or 4
    chunk = max(1000, max_id // (cores * 4))
    ranges = [(s, min(s + chunk, max_id + 1), uuid, target) for s in range(1, max_id + 1, chunk)]
    print(f"Brute-forcing userId 1..{max_id} ({cores} cores)...", file=sys.stderr)

    with Pool(cores) as pool:
        for i, result in enumerate(pool.imap_unordered(_check_range, ranges)):
            if result is not None:
                pool.terminate()
                print(f"  Found userId: {result}", file=sys.stderr)
                return result
            if (i + 1) % (cores * 2) == 0:
                print(f"  ...checked ~{min((i + 1) * chunk, max_id):,}", file=sys.stderr)
    return None


# ─── SQLCipher ───────────────────────────────────────────────────────────────

def sqlcipher_query(db_path: str, key: str, sql: str, json_mode: bool = True) -> str:
    if not os.path.exists(SQLCIPHER_BIN):
        print(f"Error: sqlcipher not found. Install: brew install sqlcipher", file=sys.stderr)
        sys.exit(1)

    cmds = [
        "PRAGMA cipher_default_compatibility = 3;",
        f"PRAGMA KEY='{key}';",
        ".mode json" if json_mode else ".mode column\n.headers on",
        sql,
    ]
    r = subprocess.run([SQLCIPHER_BIN, db_path], input="\n".join(cmds) + "\n",
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
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


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

    save_config({"uuid": uuid, "user_id": user_id, "db_path": db_path})
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
        user_id = bruteforce_user_id(uuid, target, args.max_id)
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
    """Resolve a display name for a chat room."""
    # 1) chatName if set
    if row.get("chatName"):
        return row["chatName"]

    # 2) For 1:1 or bot chats, use directChatMemberUserId
    direct_uid = row.get("directChatMemberUserId", 0)
    lid = row.get("linkId", 0)
    if direct_uid:
        try:
            users = query_json(p, k,
                f"SELECT nickName FROM NTUser WHERE userId = {direct_uid} AND linkId = {lid} LIMIT 1;")
            if users and users[0].get("nickName"):
                return users[0]["nickName"]
        except Exception:
            pass

    # 3) Fallback: gather member names from messages
    cid = row["chatId"]
    try:
        members = query_json(p, k,
            f"SELECT u.nickName FROM ("
            f"  SELECT DISTINCT authorId FROM NTChatMessage WHERE chatId = {cid} LIMIT 20"
            f") a JOIN NTUser u ON a.authorId = u.userId AND u.linkId = {lid}"
            f" LIMIT 10;")
        names = [m["nickName"] for m in members if m.get("nickName")]
        if names:
            return ", ".join(names[:4])
    except Exception:
        pass

    return "(unnamed)"


def cmd_rooms(args):
    p, k = resolve(args)
    rows = query_json(p, k,
        "SELECT chatId, type, chatName, linkId, lastLogId, directChatMemberUserId, activeMembersCount "
        "FROM NTChatRoom ORDER BY lastLogId DESC LIMIT 50;")

    for row in rows:
        row["displayName"] = _resolve_room_name(p, k, row)

    if args.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        type_map = {0: "1:1", 1: "Group", 2: "Group", 3: "Open", 4: "Open"}
        for r in rows:
            t = type_map.get(r.get("type", -1), str(r.get("type", "?")))
            name = r["displayName"]
            cid = r["chatId"]
            cnt = r.get("activeMembersCount", 0)
            suffix = f" ({cnt}명)" if cnt > 2 else ""
            print(f"  [{t:5s}] {cid} | {name}{suffix}")


def cmd_read(args):
    p, k = resolve(args)
    limit = args.limit

    where = f'WHERE m.chatId = {args.room_id}' if args.room_id else ''
    sql = f"""
        SELECT m.chatId, m.logId, m.authorId, m.type, m.sentAt, m.message,
               m.attachment, u.nickName
        FROM NTChatMessage m
        LEFT JOIN NTChatRoom r ON m.chatId = r.chatId
        LEFT JOIN NTUser u ON m.authorId = u.userId AND u.linkId = COALESCE(r.linkId, 0)
        {where}
        ORDER BY m.logId DESC LIMIT {limit};
    """
    rows = query_json(p, k, sql)
    rows.reverse()

    if args.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print(f"Messages: {len(rows)}\n")
        _print_msgs(rows)


def cmd_search(args):
    p, k = resolve(args)
    kw = args.keyword.replace("'", "''")
    sql = f"""
        SELECT m.chatId, m.logId, m.authorId, m.type, m.sentAt, m.message,
               u.nickName
        FROM NTChatMessage m
        LEFT JOIN NTChatRoom r ON m.chatId = r.chatId
        LEFT JOIN NTUser u ON m.authorId = u.userId AND u.linkId = COALESCE(r.linkId, 0)
        WHERE m.message LIKE '%{kw}%'
        ORDER BY m.logId DESC LIMIT {args.limit};
    """
    rows = query_json(p, k, sql)
    rows.reverse()

    if args.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print(f"Search '{args.keyword}': {len(rows)} results\n")
        _print_msgs(rows, show_room=True)


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
    p, k = resolve(args)

    since_dt = _parse_date(getattr(args, "since", None))
    until_dt = _parse_date(getattr(args, "until", None))

    conditions = []
    if since_dt:
        conditions.append(f"m.sentAt >= {int(since_dt.timestamp())}")
    if until_dt:
        # until is exclusive end-of-day
        until_end = until_dt + timedelta(days=1)
        conditions.append(f"m.sentAt < {int(until_end.timestamp())}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit_clause = f"LIMIT {args.limit}" if not (since_dt or until_dt) else f"LIMIT {args.limit}"

    sql = f"""
        SELECT m.chatId, m.logId, m.authorId, m.type, m.sentAt, m.message,
               u.nickName
        FROM NTChatMessage m
        LEFT JOIN NTChatRoom r ON m.chatId = r.chatId
        LEFT JOIN NTUser u ON m.authorId = u.userId AND u.linkId = COALESCE(r.linkId, 0)
        {where}
        ORDER BY m.logId DESC LIMIT {args.limit};
    """
    rows = query_json(p, k, sql)
    rows.reverse()

    if args.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        label = f"Recent {len(rows)} messages"
        if since_dt:
            label += f" since {since_dt.strftime('%Y-%m-%d')}"
        if until_dt:
            label += f" until {until_dt.strftime('%Y-%m-%d')}"
        print(f"{label}\n")
        _print_msgs(rows, show_room=True)


def cmd_users(args):
    p, k = resolve(args)
    sql = f"SELECT * FROM NTUser LIMIT {args.limit};"
    rows = query_json(p, k, sql)

    if args.format == "json":
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        print(f"Users: {len(rows)}\n")
        for r in rows:
            uid = r.get("userId", "?")
            nick = r.get("nickName", "")
            did = r.get("directChatId", "")
            print(f"  {uid} | {nick}" + (f" | chat:{did}" if did else ""))


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

    uid = bruteforce_user_id(uuid, target, args.max_id)
    if uid:
        print(json.dumps({"user_id": uid}) if args.format == "json" else f"Found! userId = {uid}")
    else:
        print(f"Not found in 1..{args.max_id}", file=sys.stderr); sys.exit(1)


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
    26: "[gift]", 27: "[system]", 51: "[liveview]",
    71: "[voicetalk]", 72: "[grouptalk]",
}


def _print_msgs(rows: list[dict], show_room: bool = False):
    for m in rows:
        ts = fmt_ts(m.get("sentAt"))
        nick = m.get("nickName") or str(m.get("authorId", "?"))
        text = m.get("message") or ""
        mtype = int(m.get("type", 1))
        tag = MSG_TYPES.get(mtype, f"[type:{mtype}]") if mtype != 1 else ""

        room = f"[{m['chatId']}] " if show_room and "chatId" in m else ""

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

def main():
    parser = argparse.ArgumentParser(
        prog="kakaotalk-reader",
        description="Read KakaoTalk Mac's encrypted local database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s info                     Show config & DB files
  %(prog)s setup                    Auto-detect userId & verify
  %(prog)s rooms                    List chat rooms
  %(prog)s read <room_id> -n 100    Read messages from a room
  %(prog)s search "keyword" -n 20   Search messages
  %(prog)s recent -n 50             Recent 50 messages
  %(prog)s recent --since today     Today's messages
  %(prog)s recent --since yesterday --until yesterday
                                    Yesterday only
  %(prog)s recent --since 2026-03-01 --until 2026-03-07
                                    Date range
  %(prog)s recent --since="-3d"     Last 3 days
  %(prog)s users                    List users/contacts
  %(prog)s sql "SELECT ..."         Raw SQL query
  %(prog)s --format json recent     JSON output for piping

Requires: brew install sqlcipher
""",
    )
    parser.add_argument("--uuid", help="Hardware UUID (auto-detected)")
    parser.add_argument("--user-id", type=int, help="KakaoTalk userId (auto-detected from plist)")
    parser.add_argument("--db-path", help="DB file path (auto-detected)")
    parser.add_argument("--format", choices=["text", "json"], default="text")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("info", help="Show config and DB files")

    p = sub.add_parser("setup", help="Auto-detect and verify setup")
    p.add_argument("--max-id", type=int, default=2_000_000)

    p = sub.add_parser("find-userid", help="Find userId")
    p.add_argument("--max-id", type=int, default=2_000_000)

    sub.add_parser("tables", help="List DB tables")

    p = sub.add_parser("schema", help="Show table schema")
    p.add_argument("table", nargs="?")

    sub.add_parser("rooms", help="List chat rooms")

    p = sub.add_parser("read", help="Read messages")
    p.add_argument("room_id", nargs="?", type=int)
    p.add_argument("-n", "--limit", type=int, default=50)

    p = sub.add_parser("search", help="Search messages")
    p.add_argument("keyword")
    p.add_argument("-n", "--limit", type=int, default=50)

    p = sub.add_parser("recent", help="Recent messages")
    p.add_argument("-n", "--limit", type=int, default=30)
    p.add_argument("--since", help="Start date (e.g. 2026-03-18, today, yesterday)")
    p.add_argument("--until", help="End date (e.g. 2026-03-19, today)")

    p = sub.add_parser("users", help="List users/contacts")
    p.add_argument("-n", "--limit", type=int, default=100)

    p = sub.add_parser("sql", help="Run raw SQL")
    p.add_argument("query")

    args = parser.parse_args()
    if not args.command:
        parser.print_help(); sys.exit(1)

    cmds = {
        "info": cmd_info, "setup": cmd_setup, "find-userid": cmd_find_userid,
        "tables": cmd_tables, "schema": cmd_schema, "rooms": cmd_rooms,
        "read": cmd_read, "search": cmd_search, "recent": cmd_recent,
        "users": cmd_users, "sql": cmd_sql,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
