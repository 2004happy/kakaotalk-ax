#!/usr/bin/env python3
"""
KakaoTalk Mac DB MCP Server — exposes kakaotalk-reader as MCP tools.

Usage:
  claude mcp add --transport stdio kakaotalk -- python3 /path/to/kakaotalk_mcp.py
"""

import json
import sys
import os

# Add src dir so we can import the reader module
sys.path.insert(0, os.path.dirname(__file__))

from mcp.server.fastmcp import FastMCP
from kakaotalk_reader import (
    resolve, query_json, find_db_files, load_config, fmt_ts,
    get_hardware_uuid, extract_user_id_from_plist, MSG_TYPES,
    _parse_date, KST, _resolve_room_name,
)
from datetime import timedelta

mcp = FastMCP("kakaotalk", instructions=(
    "카카오톡 Mac 로컬 DB를 읽는 도구입니다. "
    "채팅방 목록, 메시지 읽기, 검색, 유저 조회 등이 가능합니다."
))


# ─── Shared resolver ────────────────────────────────────────────────────────

class _Args:
    """Fake args object for resolve()."""
    uuid = None
    user_id = None
    db_path = None
    format = "json"

def _resolve():
    return resolve(_Args())


# ─── Tools ───────────────────────────────────────────────────────────────────

@mcp.tool()
def kakaotalk_rooms() -> str:
    """채팅방 목록을 조회합니다. chatId, type(0=1:1, 1=Group, 2=Group, 4=Open), 이름, 참여자 정보를 반환합니다."""
    p, k = _resolve()
    rows = query_json(p, k,
        "SELECT chatId, type, chatName, linkId, lastLogId, directChatMemberUserId, activeMembersCount "
        "FROM NTChatRoom ORDER BY lastLogId DESC LIMIT 50;")

    type_map = {0: "1:1", 1: "Group", 2: "Group", 3: "Open", 4: "Open"}
    for row in rows:
        row["roomType"] = type_map.get(row.get("type", -1), str(row.get("type")))
        row["displayName"] = _resolve_room_name(p, k, row)

    return json.dumps(rows, ensure_ascii=False, indent=2)


@mcp.tool()
def kakaotalk_read(room_id: int, limit: int = 50) -> str:
    """특정 채팅방의 메시지를 읽습니다.

    Args:
        room_id: 채팅방 ID (kakaotalk_rooms에서 chatId 확인)
        limit: 최대 메시지 수 (기본 50)
    """
    p, k = _resolve()
    rows = query_json(p, k, f"""
        SELECT m.chatId, m.logId, m.authorId, m.type, m.sentAt, m.message,
               m.attachment, u.nickName
        FROM NTChatMessage m
        LEFT JOIN NTChatRoom r ON m.chatId = r.chatId
        LEFT JOIN NTUser u ON m.authorId = u.userId AND u.linkId = COALESCE(r.linkId, 0)
        WHERE m.chatId = {room_id}
        ORDER BY m.logId DESC LIMIT {limit};
    """)
    rows.reverse()
    _enrich(rows)
    return json.dumps(rows, ensure_ascii=False, indent=2)


@mcp.tool()
def kakaotalk_recent(limit: int = 30, since: str = "", until: str = "") -> str:
    """최근 메시지를 조회합니다. 날짜 필터 가능.

    Args:
        limit: 최대 메시지 수 (기본 30)
        since: 시작 날짜 (예: "2026-03-18", "today", "yesterday", "-3d"). 생략 시 전체.
        until: 종료 날짜 (예: "2026-03-18", "today"). 해당 날짜 끝까지 포함. 생략 시 현재까지.
    """
    p, k = _resolve()

    conditions = []
    if since:
        since_dt = _parse_date(since)
        if since_dt:
            conditions.append(f"m.sentAt >= {int(since_dt.timestamp())}")
    if until:
        until_dt = _parse_date(until)
        if until_dt:
            until_end = until_dt + timedelta(days=1)
            conditions.append(f"m.sentAt < {int(until_end.timestamp())}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    rows = query_json(p, k, f"""
        SELECT m.chatId, m.logId, m.authorId, m.type, m.sentAt, m.message,
               u.nickName
        FROM NTChatMessage m
        LEFT JOIN NTChatRoom r ON m.chatId = r.chatId
        LEFT JOIN NTUser u ON m.authorId = u.userId AND u.linkId = COALESCE(r.linkId, 0)
        {where}
        ORDER BY m.logId DESC LIMIT {limit};
    """)
    rows.reverse()
    _enrich(rows)
    return json.dumps(rows, ensure_ascii=False, indent=2)


@mcp.tool()
def kakaotalk_search(keyword: str, limit: int = 50) -> str:
    """메시지를 키워드로 검색합니다.

    Args:
        keyword: 검색할 키워드
        limit: 최대 결과 수 (기본 50)
    """
    p, k = _resolve()
    kw = keyword.replace("'", "''")
    rows = query_json(p, k, f"""
        SELECT m.chatId, m.logId, m.authorId, m.type, m.sentAt, m.message,
               u.nickName
        FROM NTChatMessage m
        LEFT JOIN NTChatRoom r ON m.chatId = r.chatId
        LEFT JOIN NTUser u ON m.authorId = u.userId AND u.linkId = COALESCE(r.linkId, 0)
        WHERE m.message LIKE '%{kw}%'
        ORDER BY m.logId DESC LIMIT {limit};
    """)
    rows.reverse()
    _enrich(rows)
    return json.dumps(rows, ensure_ascii=False, indent=2)


@mcp.tool()
def kakaotalk_users(limit: int = 100) -> str:
    """DB에 저장된 유저/연락처 목록을 조회합니다.

    Args:
        limit: 최대 유저 수 (기본 100)
    """
    p, k = _resolve()
    rows = query_json(p, k, f"SELECT * FROM NTUser LIMIT {limit};")
    return json.dumps(rows, ensure_ascii=False, indent=2)


@mcp.tool()
def kakaotalk_sql(query: str) -> str:
    """카카오톡 DB에 직접 SQL 쿼리를 실행합니다.

    주요 테이블:
    - NTChatRoom: chatId, type, chatName, linkId, lastLogId
    - NTChatMessage: chatId, logId, authorId, type, sentAt, message, attachment
    - NTUser: userId, linkId, nickName

    JOIN 시 주의: NTUser는 (userId, linkId) 복합키. NTChatRoom.linkId와 매칭해야 중복 방지.

    Args:
        query: SQL 쿼리문
    """
    p, k = _resolve()
    rows = query_json(p, k, query)
    return json.dumps(rows, ensure_ascii=False, indent=2)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _enrich(rows: list[dict]):
    """Add human-readable fields to message rows."""
    for m in rows:
        if m.get("sentAt"):
            m["time"] = fmt_ts(m["sentAt"])
        mtype = m.get("type")
        if mtype and mtype != 1:
            m["typeLabel"] = MSG_TYPES.get(mtype, f"type:{mtype}")


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
