"""Shared, read-only queries for the CLI and MCP interfaces."""

import json
import plistlib
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import kakaotalk_reader as db


ROOM_TYPES = {0: "direct", 1: "group", 2: "channel", 3: "open_direct", 4: "open_group", 5: "self"}
MESSAGE_TYPES = {0: "system", 1: "text", 2: "image", 3: "video", 4: "audio", 5: "contact",
                 6: "file", 12: "sticker", 14: "location", 16: "schedule", 18: "call",
                 22: "emoji", 23: "notice", 24: "reply", 26: "reply", 27: "album",
                 51: "media", 71: "card", 72: "notification_card"}


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def number(value, name, low=0, high=2**63 - 1):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


def parsed_json(value, default=None):
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value or len(value) > 2_000_000:
        return default
    try:
        return json.loads(value)
    except (ValueError, RecursionError):
        return default


def plist_array(value):
    try:
        result = plistlib.loads(bytes.fromhex(value)) if value else []
        return result if isinstance(result, list) else []
    except (ValueError, TypeError, plistlib.InvalidFileException):
        return []


def date_bound(value, end=False, now=None):
    """Calendar dates include the end day; ISO timestamps remain exact."""
    if not value:
        return None
    now = now or datetime.now(db.KST)
    value = value.strip()
    lowered = value.lower()
    if lowered == "now":
        return now
    rolling = re.fullmatch(r"last(\d+)([dhm])", lowered)
    if rolling:
        count = int(rolling[1])
        if count > 36500:
            raise ValueError("Relative time range is too large")
        return now - timedelta(**{{"d": "days", "h": "hours", "m": "minutes"}[rolling[2]]: count})
    if lowered in ("today", "yesterday") or re.fullmatch(r"-\d+d", lowered):
        days = 0 if lowered == "today" else 1 if lowered == "yesterday" else int(lowered[1:-1])
        result = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        return result + timedelta(days=1) if end else result
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Use YYYY-MM-DD, ISO timestamp, today, yesterday, -7d, last7d, or now") from error
    if result.tzinfo is None:
        result = result.replace(tzinfo=db.KST)
    if end and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        result += timedelta(days=1)
    return result


def time_window(since="", until=""):
    now = datetime.now(db.KST)
    start, finish = date_bound(since, now=now), date_bound(until, end=True, now=now)
    if start and finish and start >= finish:
        raise ValueError("since must be earlier than until")
    return start, finish


def card_content(attachment):
    texts, buttons = [], []

    def walk(value, depth=0):
        if depth > 15:
            return
        if isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
        elif isinstance(value, dict):
            td = value.get("TD")
            if isinstance(td, dict):
                for field in ("T", "D"):
                    text = td.get(field)
                    if isinstance(text, str) and text.strip() and text not in texts:
                        texts.append(text.strip())
            bu = value.get("BU")
            if isinstance(bu, dict) and isinstance(bu.get("T"), str):
                if bu["T"] not in buttons:
                    buttons.append(bu["T"])
            for key, item in value.items():
                if key in ("text", "title", "description") and isinstance(item, str) and item.strip():
                    if item.strip() not in texts:
                        texts.append(item.strip())
                elif isinstance(item, (dict, list)):
                    walk(item, depth + 1)

    walk(attachment.get("C", {}))
    return texts, buttons


def media_entries(message):
    attachment = parsed_json(message.get("attachment"), {})
    if not isinstance(attachment, dict):
        return []
    items = []
    urls = attachment.get("imageUrls")
    if isinstance(urls, list):
        for i, url in enumerate(urls):
            if isinstance(url, str):
                items.append({"kind": "image", "_url": url, "filename": f"image-{i + 1}",
                              "mimeType": "image/*"})
    elif isinstance(attachment.get("url"), str):
        kind = MESSAGE_TYPES.get(message.get("type"), "file")
        mime = attachment.get("mt") or attachment.get("mimeType") or ""
        if kind == "media":
            kind = "image" if str(mime).startswith("image/") else "file"
        items.append({"kind": kind, "_url": attachment["url"],
                      "filename": attachment.get("name") or attachment.get("fileName") or kind,
                      "mimeType": mime, "size": attachment.get("s") or attachment.get("size"),
                      "width": attachment.get("w"), "height": attachment.get("h")})
    path = message.get("localFilePath")
    if path and not items:
        items.append({"kind": MESSAGE_TYPES.get(message.get("type"), "file"),
                      "filename": attachment.get("name") or "attachment", "_path": path})
    elif path and len(items) == 1:
        items[0]["_path"] = path
    for i, item in enumerate(items):
        item["index"] = i
    return items


def visible_media(items):
    return [{**{k: v for k, v in item.items() if not k.startswith("_")},
             "remoteReferenceStored": bool(item.get("_url")),
             "localReferenceStored": bool(item.get("_path"))}
            for item in items]


class Reader:
    def __init__(self, path=None, key=None, user_id=None):
        self.path, self.key = (path, key) if path and key else db.resolve(SimpleNamespace())
        self.user_id = user_id if user_id is not None else db.load_config().get("user_id")
        self._columns = {}
        self._rooms = None
        self._users = None
        self._tables = {row["name"] for row in self.query("SELECT name FROM sqlite_master WHERE type='table';")}

    def query(self, sql):
        return db.query_json(self.path, self.key, sql)

    def columns(self, table):
        if table not in self._columns:
            self._columns[table] = {r["name"] for r in self.query(f'PRAGMA table_info("{table}");')}
        return self._columns[table]

    def selected(self, table, fields):
        existing = self.columns(table)
        return ",".join(f'"{name}"' if name in existing else f'{fallback} AS "{name}"'
                        for name, fallback in fields.items())

    def user_map(self):
        if self._users is None:
            fields = {"userId": "0", "linkId": "0", "nickName": "''", "friendNickName": "''",
                      "displayName": "''", "favorite": "0", "hidden": "0"}
            rows = self.query(f"SELECT {self.selected('NTUser', fields)} FROM NTUser;")
            for row in rows:
                row["name"] = row.get("friendNickName") or row.get("displayName") or row.get("nickName") or "(이름 없음)"
            self._users = {(r["userId"], r["linkId"]): r for r in rows}
        return self._users

    def all_rooms(self):
        if self._rooms is not None:
            return self._rooms
        fields = {"chatId": "0", "linkId": "0", "type": "-1", "chatName": "''", "lastLogId": "0",
                  "directChatMemberUserId": "0", "activeMembersCount": "0", "countOfNewMessage": "0",
                  "favorite": "0", "hidden": "0", "pin": "-1", "lastSeenLogId": "0", "lastSyncLogId": "0"}
        columns = self.selected("NTChatRoom", fields)
        member_column = ',hex(displayMemberIds) AS memberIds' if 'displayMemberIds' in self.columns('NTChatRoom') else ",'' AS memberIds"
        member_column += ',hex(watermarkKeys) AS membershipIds' if 'watermarkKeys' in self.columns('NTChatRoom') else ",'' AS membershipIds"
        rooms = self.query(f"SELECT {columns}{member_column} FROM NTChatRoom ORDER BY lastLogId DESC;")
        aliases = {}
        if "NTChatMeta" in self._tables:
            fields = self.selected("NTChatMeta", {"chatId": "0", "type": "0", "content": "''", "groupNickname": "''"})
            for row in self.query(f"SELECT {fields} FROM NTChatMeta WHERE type=3;"):
                if row.get("groupNickname") or row.get("content"):
                    aliases[row["chatId"]] = row.get("groupNickname") or row["content"]
        links = {r["linkId"]: r.get("linkName") for r in self.query("SELECT linkId,linkName FROM NTOpenLink;")} if "NTOpenLink" in self._tables else {}
        users = self.user_map()
        for room in rooms:
            cid, lid = room["chatId"], room["linkId"]
            displayed = [v for v in plist_array(room["memberIds"]) if isinstance(v, int)]
            membership = [v for v in plist_array(room.pop("membershipIds")) if isinstance(v, int)]
            complete = len(set(membership)) == room["activeMembersCount"] and bool(membership)
            room["memberIds"] = list(dict.fromkeys(membership if complete else displayed))
            room["membersSource"] = "membership_watermarks" if complete else "display_members"
            room["roomType"] = ROOM_TYPES.get(room["type"], "unknown")
            name, source = aliases.get(cid), "local_nickname"
            if not name:
                name, source = room.get("chatName"), "room_name"
            if not name:
                name, source = links.get(lid), "open_link"
            if not name and room["type"] == 5:
                name, source = "나와의 채팅", "self"
            if not name and room.get("directChatMemberUserId"):
                direct = users.get((room["directChatMemberUserId"], lid), {})
                name, source = direct.get("name"), "contact"
            if not name:
                names = [users[(uid, lid)]["name"] for uid in room["memberIds"] if (uid, lid) in users]
                if not names:
                    authors = self.query(f"SELECT DISTINCT authorId FROM NTChatMessage WHERE chatId={int(cid)} LIMIT 20;")
                    names = [users[(a["authorId"], lid)]["name"] for a in authors if (a["authorId"], lid) in users]
                name, source = ", ".join(dict.fromkeys(names))[:200] or "(이름 없음)", "participants"
            room["displayName"], room["nameSource"] = name, source
            room["nameExact"] = source != "participants"
        self._rooms = rooms
        return rooms

    @staticmethod
    def page(items, limit, offset, **extra):
        number(limit, "limit", 1, 1000)
        number(offset, "offset")
        total = len(items)
        selected = items[offset:offset + limit]
        return {"items": selected, "total": total, "offset": offset, "limit": limit,
                "hasMore": offset + len(selected) < total,
                "nextOffset": offset + len(selected) if offset + len(selected) < total else None,
                "source": "local_database", **extra}

    def rooms(self, limit=100, offset=0, query="", room_type=-1, unread_only=False):
        rows = self.all_rooms()
        if query:
            rows = [r for r in rows if query.casefold() in r["displayName"].casefold()]
        if room_type != -1:
            number(room_type, "room_type")
            rows = [r for r in rows if r["type"] == room_type]
        if unread_only:
            rows = [r for r in rows if r["countOfNewMessage"] > 0]
        return self.page(rows, limit, offset)

    def find_room(self, room_id=0, room_name=""):
        if room_id:
            number(room_id, "room_id", 1)
            candidates = [r for r in self.all_rooms() if r["chatId"] == room_id]
        elif room_name:
            candidates = [r for r in self.all_rooms() if r["displayName"].casefold() == room_name.casefold()]
            if not candidates:
                candidates = [r for r in self.all_rooms() if room_name.casefold() in r["displayName"].casefold()]
        else:
            raise ValueError("Provide room_id or room_name")
        if not candidates:
            raise ValueError("Room not found in the local database")
        if len(candidates) != 1:
            raise ValueError("Room name is ambiguous; select chatId from kakaotalk_rooms")
        if room_id and room_name and room_name.casefold() not in candidates[0]["displayName"].casefold():
            raise ValueError("room_id and room_name refer to different rooms")
        return candidates[0]

    def room(self, room_id=0, room_name=""):
        room = dict(self.find_room(room_id, room_name))
        users = self.user_map()
        room["members"] = [users.get((uid, room["linkId"]), {"userId": uid, "name": "(이름 없음)"}) for uid in room["memberIds"]]
        room["membersComplete"] = room["membersSource"] == "membership_watermarks" and len(room["members"]) == room["activeMembersCount"]
        room["memberNamesComplete"] = all((uid, room["linkId"]) in users for uid in room["memberIds"])
        room["notices"] = self.notices(room["chatId"])["items"]
        return room

    def _message_sql(self, where="1", suffix=""):
        fields = {"chatId": "0", "logId": "0", "msgId": "0", "authorId": "0", "type": "-1",
                  "sentAt": "0", "message": "''", "attachment": "''", "supplement": "''",
                  "localFilePath": "''", "status": "0", "revision": "0"}
        return f"SELECT {self.selected('NTChatMessage', fields)} FROM NTChatMessage WHERE {where} {suffix};"

    def enrich(self, message):
        room = next((r for r in self.all_rooms() if r["chatId"] == message["chatId"]), {})
        user = self.user_map().get((message["authorId"], room.get("linkId", 0)), {})
        raw = message.get("message") or ""
        attachment = parsed_json(message.get("attachment"), {})
        if not isinstance(attachment, dict):
            attachment = {}
        texts, buttons = card_content(attachment)
        parts = [raw] if raw else []
        for text in texts:
            if not any(text in existing for existing in parts):
                parts = [existing for existing in parts if existing not in text]
                parts.append(text)
        result = {k: v for k, v in message.items() if k not in ("attachment", "supplement", "localFilePath")}
        result.update(roomName=room.get("displayName", "(이름 없음)"), roomNameSource=room.get("nameSource", "unknown"),
                      nickName=user.get("name", "(이름 없음)"), isMe=message["authorId"] == self.user_id,
                      time=db.fmt_ts(message.get("sentAt")), messageKind=MESSAGE_TYPES.get(message["type"], "unknown"),
                      text="\n\n".join(parts), media=visible_media(media_entries(message)))
        if buttons:
            result["buttons"] = buttons
        if "src_message" in attachment:
            result["reply"] = {"text": attachment.get("src_message"), "logId": attachment.get("src_logId"),
                               "authorId": attachment.get("src_userId"), "type": attachment.get("src_type")}
        if message["type"] == 0:
            event = parsed_json(raw, {})
            if isinstance(event, dict):
                kind = {4: "join", 2: "leave"}.get(event.get("feedType"), "system")
                members = event.get("members") or [event.get("member", {})]
                names = [m["nickName"] for m in members if isinstance(m, dict) and m.get("nickName")]
                result["systemEvent"] = {"kind": kind, "members": names}
                result["text"] = (", ".join(names) + (" 입장" if kind == "join" else " 퇴장")) if names and kind in ("join", "leave") else "시스템 알림"
        return result

    def messages(self, room_id=0, room_name="", limit=50, offset=0, since="", until="",
                 keyword="", before_log_id=0, after_log_id=0):
        number(limit, "limit", 1, 1000)
        number(offset, "offset")
        start, finish = time_window(since, until)
        conditions = ["1"]
        room = self.find_room(room_id, room_name) if room_id or room_name else None
        if room:
            conditions.append(f"chatId={room['chatId']}")
        stamp = "(CASE WHEN sentAt>=1000000000000 THEN sentAt/1000.0 ELSE sentAt END)"
        if start:
            conditions.append(f"{stamp}>={start.timestamp()}")
        if finish:
            conditions.append(f"{stamp}<{finish.timestamp()}")
        for value, symbol, label in [(before_log_id, "<", "before_log_id"), (after_log_id, ">", "after_log_id")]:
            if value:
                conditions.append(f"logId{symbol}{number(value, label, 1)}")
        where = " AND ".join(conditions)
        order = f"ORDER BY {stamp} DESC,logId DESC"
        if keyword:
            rows = self.query(self._message_sql(where, order + " LIMIT 20001"))
            if len(rows) > 20000:
                raise ValueError("Full-text search exceeds 20,000 local records; narrow the date range or room")
            rows = [self.enrich(r) for r in rows]
            rows = [r for r in rows if keyword.casefold() in r["text"].casefold() or keyword.casefold() in str(r.get("reply", {}).get("text", "")).casefold()]
            page = self.page(rows, limit, offset)
        else:
            total = self.query(f"SELECT count(*) AS n FROM NTChatMessage WHERE {where};")[0]["n"]
            rows = self.query(self._message_sql(where, f"{order} LIMIT {limit} OFFSET {offset}"))
            page = {"items": [self.enrich(r) for r in rows], "total": total, "offset": offset,
                    "limit": limit, "hasMore": offset + len(rows) < total,
                    "nextOffset": offset + len(rows) if offset + len(rows) < total else None,
                    "source": "local_database"}
        page["items"].reverse()
        page.update(since=start.isoformat() if start else None, until=finish.isoformat() if finish else None,
                    order="chronological_within_newest_page", coverage="Messages currently stored on this Mac; unopened rooms and older history may be incomplete")
        return page

    def raw_message(self, room_id, log_id):
        room_id, log_id = number(room_id, "room_id", 1), number(log_id, "log_id", 1)
        rows = self.query(self._message_sql(f"chatId={room_id} AND logId={log_id}", "ORDER BY msgId DESC LIMIT 1" if "msgId" in self.columns("NTChatMessage") else "LIMIT 1"))
        if not rows:
            raise ValueError("Message not found in the local database")
        return rows[0]

    def message(self, room_id, log_id):
        return self.enrich(self.raw_message(room_id, log_id))

    def users(self, query="", limit=100, offset=0):
        rows = list(self.user_map().values())
        if query:
            rows = [r for r in rows if query.casefold() in r["name"].casefold()]
        rows.sort(key=lambda r: (r["name"].casefold(), r["userId"], r["linkId"]))
        return self.page(rows, limit, offset)

    def notices(self, room_id=0, limit=100, offset=0):
        if "NTChatMeta" not in self._tables:
            return self.page([], limit, offset)
        where = f" AND chatId={number(room_id, 'room_id', 1)}" if room_id else ""
        rows = self.query("SELECT chatId,type,content,noticeText,updatedAt FROM NTChatMeta WHERE type=1" + where + " ORDER BY updatedAt DESC;")
        for row in rows:
            row["text"] = row.get("noticeText") or row.get("content") or ""
        return self.page(rows, limit, offset)

    def folders(self):
        if "NTChatFolder" not in self._tables:
            return {"items": [], "source": "local_database"}
        rows = self.query("SELECT folderId,name,type,sortOrder,hidden,hex(chatIds) AS encoded FROM NTChatFolder ORDER BY sortOrder;")
        for row in rows:
            row["chatIds"] = plist_array(row.pop("encoded"))
        return {"items": rows, "source": "local_database"}

    def bookmarks(self, room_id=0, limit=100, offset=0):
        if "NTBookmark" not in self._tables:
            return self.page([], limit, offset)
        where = f"WHERE chatId={number(room_id, 'room_id', 1)}" if room_id else ""
        rows = self.query("SELECT chatId,logId,type,displayName,createAt FROM NTBookmark " + where + " ORDER BY createAt DESC;")
        return self.page(rows, limit, offset)

    def events(self, since="today", until="", room_id=0, limit=100, offset=0):
        if "NTEvent" not in self._tables:
            return self.page([], limit, offset)
        start, finish = time_window(since, until)
        conditions = ["1"]
        if room_id:
            conditions.append(f"chatId={number(room_id, 'room_id', 1)}")
        if start:
            conditions.append(f"coalesce(endAt,startAt)>={int(start.timestamp())}")
        if finish:
            conditions.append(f"startAt<{int(finish.timestamp())}")
        rows = self.query("SELECT eventId,chatId,subject,startAt,endAt,isAllDay,note,memo,locationInfo,timeZone,recurrenceRule FROM NTEvent WHERE " + " AND ".join(conditions) + " ORDER BY startAt;")
        for row in rows:
            row["startTime"], row["endTime"] = db.fmt_ts(row["startAt"]), db.fmt_ts(row["endAt"])
        return self.page(rows, limit, offset, coverage="Local calendar entries; recurring occurrences are not expanded")

    def activity(self, since="last7d", until="now"):
        start, finish = time_window(since, until)
        stamp = "(CASE WHEN sentAt>=1000000000000 THEN sentAt/1000.0 ELSE sentAt END)"
        where = ["1"]
        if start:
            where.append(f"{stamp}>={start.timestamp()}")
        if finish:
            where.append(f"{stamp}<{finish.timestamp()}")
        uid = int(self.user_id or 0)
        rows = self.query(f"SELECT chatId,count(*) AS messageCount,sum(authorId={uid}) AS sentByMe,min({stamp}) AS firstAt,max({stamp}) AS lastAt FROM NTChatMessage WHERE {' AND '.join(where)} GROUP BY chatId ORDER BY messageCount DESC;")
        rooms = {r["chatId"]: r for r in self.all_rooms()}
        for row in rows:
            room = rooms.get(row["chatId"], {})
            row.update(roomName=room.get("displayName", "(이름 없음)"), nameSource=room.get("nameSource", "unknown"),
                       roomType=room.get("roomType", "unknown"), firstTime=db.fmt_ts(row["firstAt"]), lastTime=db.fmt_ts(row["lastAt"]))
        return {"items": rows, "messageCount": sum(r["messageCount"] for r in rows),
                "sentByMe": sum(r["sentByMe"] for r in rows), "roomCount": len(rows),
                "since": start.isoformat() if start else None, "until": finish.isoformat() if finish else None,
                "source": "local_database", "coverage": "Only messages currently stored on this Mac"}

    def status(self):
        stamp = "CASE WHEN sentAt>=1000000000000 THEN sentAt/1000.0 ELSE sentAt END"
        rows = self.query(f"SELECT count(*) AS messages,min({stamp}) AS oldest,max({stamp}) AS newest FROM NTChatMessage;")
        rooms = self.all_rooms()
        return {"readOnly": True, "sendingSupported": False, "roomCount": len(rooms), **rows[0],
                "oldestTime": db.fmt_ts(rows[0]["oldest"]), "newestTime": db.fmt_ts(rows[0]["newest"]),
                "exactRoomNames": sum(r["nameExact"] for r in rooms),
                "capabilities": ["room_names", "pagination", "members", "message_history", "full_card_text", "reply_context",
                                 "media", "unread_counts", "notices", "folders", "bookmarks", "calendar", "activity"],
                "coverage": "Local cache only; this does not fetch missing mobile history or expand recurring calendar events"}
