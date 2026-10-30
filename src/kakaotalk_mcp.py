#!/usr/bin/env python3
"""KakaoTalk MCP: read-only local history and independent tablet LOCO sending."""

import base64
import json

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent, ToolAnnotations

from kakaotalk_service import Reader, media_entries, visible_media
from kakaotalk_media import retrieve

mcp = FastMCP("kakaotalk", instructions=(
    "카카오톡 로컬 DB 조회와 별도 Android 태블릿 LOCO 전송 도구입니다. "
    "기존 PC 인증·연결을 재사용하지 않고, 기기 등록을 완료한 태블릿 세션으로 기존 방에 전송합니다. "
    "전송은 사용자의 명시적인 수신자·본문 요청이 있을 때만 수행하고 outcome_unknown이면 재시도하지 마세요. "
    "방 이름은 로컬 별칭, 오픈채팅 정보, 연락처에서 찾으며 nameSource/nameExact를 확인하세요. "
    "목록에는 total/hasMore/nextOffset이 있으니 한 페이지만 읽고 전체라고 판단하지 마세요. "
    "text는 카드 본문까지 포함한 읽기용 내용이며 message는 원래 미리보기입니다. "
    "컴퓨터 유즈·AX·앱 조작을 사용하지 않습니다. "
    "로컬에 동기화되지 않은 모바일 전체 기록은 보장하지 않습니다."
))
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
EXTERNAL_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)


def output(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


@mcp.tool(annotations=READ)
def kakaotalk_status() -> str:
    """로컬 기록 범위와 지원 기능을 확인합니다. DB는 읽기 전용이며 전송은 별도 도구입니다."""
    result = Reader().status()
    result.update(databaseReadOnly=True, sendingSupported=False,
                  sendingBlockedReason="desktop_session_conflict")
    try:
        from kakaotalk_tablet import tablet_status
        result["tablet"] = tablet_status()
        result["pcSendingBlockedReason"] = "desktop_session_conflict"
        result["sendingSupported"] = result["tablet"]["sendingSupported"]
        result["sendingBlockedReason"] = None if result["sendingSupported"] else result["tablet"]["status"]
        if result["sendingSupported"]:
            result.update(readOnly=False, sendingScope="existing_rooms", sendingTransport="loco_tablet_tls")
            result["capabilities"].append("send_tablet")
    except ImportError:
        result["tablet"] = {"status": "install_send_extra"}
    return output(result)


@mcp.tool(annotations=SEND)
def kakaotalk_send(room_id: int, text: str) -> str:
    """태블릿 보조기기 세션으로 기존 1:1·단톡방 등 지정 room_id에 텍스트 한 건을 보냅니다.

    앱 조작·AX·새 PC 로그인은 사용하지 않습니다. 최초 터미널의 kakaotalk-tablet login이 필요합니다.
    수신자와 본문을 명시적으로 요청받았을 때만 사용하세요. outcome_unknown은 자동 재시도 금지입니다.
    """
    try:
        from kakaotalk_tablet import send_message
    except ImportError:
        return output({"status": "install_send_extra", "messagesSent": 0})
    return output(send_message(room_id, text))


@mcp.tool(annotations=READ)
def kakaotalk_rooms(limit: int = 100, offset: int = 0, query: str = "", room_type: int = -1,
                   unread_only: bool = False) -> str:
    """정확한 방 이름·이름 출처·참여자 수·안 읽은 수를 조회합니다. query는 방 이름 검색.

    room_type: -1 전체, 0 개인, 1 단톡, 2 채널, 3 오픈개인, 4 오픈단톡, 5 나와의 채팅.
    hasMore가 참이면 nextOffset으로 다음 페이지를 조회하세요. limit은 1~1000.
    """
    return output(Reader().rooms(limit, offset, query, room_type, unread_only))


@mcp.tool(annotations=READ)
def kakaotalk_room(room_id: int = 0, room_name: str = "") -> str:
    """방 이름/ID로 방 정보·확인 가능한 참여자·공지·읽음 상태를 조회합니다. 동명이면 ID가 필요합니다."""
    return output(Reader().room(room_id, room_name))


@mcp.tool(annotations=READ)
def kakaotalk_read(room_id: int = 0, limit: int = 50, room_name: str = "", offset: int = 0,
                   since: str = "", until: str = "", before_log_id: int = 0, after_log_id: int = 0) -> str:
    """특정 방 메시지와 카드 본문·답장 맥락·첨부파일 목록을 읽습니다.

    방 ID 또는 이름 중 하나를 지정하세요. 날짜는 YYYY-MM-DD, ISO 시각, today, yesterday,
    last7d(최근 168시간), -7d(7일 전 자정), now. until 날짜는 해당 날짜 끝까지 포함합니다.
    최신 페이지를 시간순으로 반환하며 hasMore/nextOffset으로 누락을 확인할 수 있습니다.
    """
    if not room_id and not room_name:
        raise ValueError("Provide room_id or room_name")
    return output(Reader().messages(room_id=room_id, room_name=room_name, limit=limit, offset=offset,
                                   since=since, until=until, before_log_id=before_log_id,
                                   after_log_id=after_log_id))


@mcp.tool(annotations=READ)
def kakaotalk_recent(limit: int = 100, since: str = "", until: str = "", offset: int = 0) -> str:
    """전체 방의 최근 메시지를 방 이름과 함께 조회합니다. 날짜 필터와 페이지 조회를 지원합니다."""
    return output(Reader().messages(limit=limit, since=since, until=until, offset=offset))


@mcp.tool(annotations=READ)
def kakaotalk_search(keyword: str, limit: int = 100, room_id: int = 0, room_name: str = "",
                     since: str = "", until: str = "", offset: int = 0) -> str:
    """방/기간을 지정해 본문·카드 전체 텍스트·인용한 답장 내용을 검색합니다.

    %, _도 문자 그대로 검색합니다. 범위가 2만 건을 넘으면 방/기간을 좁혀주세요.
    결과가 없으면 items=[]을 반환합니다.
    """
    if not keyword:
        raise ValueError("keyword must not be empty")
    return output(Reader().messages(keyword=keyword, limit=limit, room_id=room_id, room_name=room_name,
                                   since=since, until=until, offset=offset))


@mcp.tool(annotations=READ)
def kakaotalk_message(room_id: int, log_id: int) -> str:
    """메시지 한 건의 전체 텍스트·답장·카드 버튼 이름·첨부파일 메타데이터를 읽습니다."""
    return output(Reader().message(room_id, log_id))


@mcp.tool(annotations=READ)
def kakaotalk_users(limit: int = 100, offset: int = 0, query: str = "") -> str:
    """연락처/오픈채팅 이름을 검색합니다. 전화번호·인증 정보는 포함하지 않습니다."""
    return output(Reader().users(query, limit, offset))


@mcp.tool(annotations=READ)
def kakaotalk_activity(since: str = "last7d", until: str = "now") -> str:
    """지정 기간의 방별 메시지 수·직접 보낸 수·최초/최근 시각을 집계합니다. 기본 최근 168시간."""
    return output(Reader().activity(since, until))


@mcp.tool(annotations=READ)
def kakaotalk_notices(room_id: int = 0, limit: int = 100, offset: int = 0) -> str:
    """방 공지를 읽습니다. room_id를 생략하면 로컬에 저장된 전체 공지를 조회합니다."""
    return output(Reader().notices(room_id, limit, offset))


@mcp.tool(annotations=READ)
def kakaotalk_folders() -> str:
    """카카오톡의 채팅방 폴더 이름과 폴더별 방 ID를 읽습니다."""
    return output(Reader().folders())


@mcp.tool(annotations=READ)
def kakaotalk_bookmarks(room_id: int = 0, limit: int = 100, offset: int = 0) -> str:
    """로컬에 저장된 메시지 북마크를 조회합니다."""
    return output(Reader().bookmarks(room_id, limit, offset))


@mcp.tool(annotations=READ)
def kakaotalk_events(since: str = "today", until: str = "", room_id: int = 0,
                     limit: int = 100, offset: int = 0) -> str:
    """로컬 톡캘린더 일정을 조회합니다. 반복 일정의 개별 발생일은 자동 전개하지 않습니다."""
    return output(Reader().events(since, until, room_id, limit, offset))


@mcp.tool(annotations=READ)
def kakaotalk_media(room_id: int, log_id: int) -> str:
    """메시지의 사진·앨범·동영상·파일 목록을 조회합니다. 파일 다운로드나 외부 통신은 하지 않습니다."""
    message = Reader().raw_message(room_id, log_id)
    return output({"items": visible_media(media_entries(message)), "source": "local_database"})


@mcp.tool(annotations=EXTERNAL_READ)
def kakaotalk_get_media(room_id: int, log_id: int, index: int = 0, download: bool = True,
                       max_bytes: int = 25 * 1024 * 1024) -> list[TextContent | ImageContent]:
    """메시지에 연결된 첨부파일 하나를 읽습니다. 사진은 이미지로 반환해 내용을 볼 수 있습니다.

    로컬 파일을 우선 사용하고, download=True면 저장된 Kakao CDN 주소에서만 다운로드합니다.
    만료된 주소는 media_unavailable로 표시합니다. 최대 25MB, 인라인 이미지는 최대 8MB.
    원문 URL/인증 토큰을 응답에 노출하지 않습니다. 파일은 본인 전용 로컬 캐시에 저장합니다.
    """
    info, data = retrieve(Reader(), room_id, log_id, index, download, max_bytes)
    result = [TextContent(type="text", text=output(info))]
    if data is not None and info.get("imageEmbeddable"):
        result.append(ImageContent(type="image", data=base64.b64encode(data).decode("ascii"), mimeType=info["mimeType"]))
    return result


@mcp.tool(annotations=READ)
def kakaotalk_sql(query: str) -> str:
    """고급 읽기 전용 SELECT/CTE/조회 PRAGMA를 한 문장씩 실행합니다.

    DB 변경, 파일 읽기/쓰기 함수, shell 명령, 외부 DB 연결과 확장 로드는 금지합니다.
    """
    rows = Reader().query(query)
    return output({"items": rows, "total": len(rows), "source": "local_database"})


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
