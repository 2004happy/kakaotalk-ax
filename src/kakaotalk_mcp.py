#!/usr/bin/env python3
"""KakaoTalk MCP: native Accessibility reads, navigation and one-shot sends."""

import json

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("kakaotalk", instructions=(
    "카카오톡의 모든 기능은 kakaotalk_ax_* 네이티브 접근성 헬퍼를 사용합니다. "
    "방 열기·닫기·읽기·일괄 조회·검색·이전 대화 요청·전송을 지원합니다. "
    "DB 조회·계정 키 복구·비공식 프로토콜·별도 인증·기기 등록은 제공하지 않습니다. "
    "여러 방은 kakaotalk_ax_read_many로 열고 병렬 조회하세요. 별도 제어 코드를 작성하지 마세요. "
    "AX 읽기는 정확한 제목의 열린 방에서 노출된 행만 읽으며 과거 전체 기록을 보장하지 않습니다. "
    "AX 페이지는 snapshotId를 다음 조회의 snapshot_id로 전달하고 snapshot_changed면 처음부터 조회하세요. "
    "방 목록은 보이는 행이며 reportedRowCount와 전체 목록을 혼동하지 마세요. 동명 방은 중단합니다. "
    "사용자가 명시적으로 요청한 방과 본문에만 전송하고 요청별 operation_id를 한 번 정해 재사용하세요. "
    "전송은 접근성 입력창과 AXPress 버튼을 사용하며 기존 초안을 보존합니다. "
    "outcome_unknown이나 타임아웃이면 원래 ID로 kakaotalk_ax_send_result만 조회하고 재전송하지 마세요. "
    "observed_in_ax는 새 텍스트와 빈 입력창의 관찰이며 작성자·서버 ACK·읽음 확인이 아닙니다. "
    "스크린샷·OCR·Computer Use로 헬퍼를 대체하지 마세요."
))
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
UI = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
NAVIGATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)


def output(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


@mcp.tool(annotations=READ)
def kakaotalk_ax_windows() -> str:
    """DB 없이 현재 열린 카카오톡 창 제목과 AX 읽기 가능 여부를 확인합니다.

    방을 열거나 창을 올리지 않으며, 메시지 본문·입력 중인 초안을 읽지 않습니다.
    readable=true인 창의 정확한 windowTitle을 kakaotalk_ax_read에 전달하세요.
    닫힌 방 목록이나 계정의 모든 방을 반환하는 도구가 아닙니다.
    """
    from kakaotalk_automation import NativeAX
    return output(NativeAX().windows())


@mcp.tool(annotations=READ)
def kakaotalk_ax_read(window_title: str, limit: int = 50, offset: int = 0, snapshot_id: str = "") -> str:
    """열린 방의 노출된 AX 행을 DB·스크린샷 없이 읽습니다. limit 1..500.

    최신 행부터 페이지를 선택하고 페이지 안에서는 화면의 행 순서를 유지합니다.
    다음 페이지는 nextOffset과 snapshotId를 offset/snapshot_id로 전달하세요.
    새 메시지 등으로 행이 바뀌면 snapshot_changed를 반환합니다. offset=0부터 다시 읽으세요.
    text가 없는 행도 unclassified로 보존합니다. 작성자는 아직 판별하지 않으며 미노출 시각도 null입니다.
    hasMore는 수집한 최대 1000행 안의 페이지 유무이며, 전체 대화의 완전성을 뜻하지 않습니다.
    창 열기·포커스 변경·스크롤·초안 변경·메시지 전송·DB 폴백은 하지 않습니다.
    """
    from kakaotalk_automation import NativeAX
    return output(NativeAX().read_messages(window_title, limit, offset, snapshot_id))


@mcp.tool(annotations=UI)
def kakaotalk_ax_rooms(query: str = "") -> str:
    """AX 채팅 목록의 방 이름·미리보기·본인 방 표시를 읽습니다. 필요하면 채팅 탭으로 전환합니다.

    query는 현재 노출된 이름에 대한 부분 문자열 필터입니다. 모든 방의 완전한 목록이 아닙니다.
    닫힌 방을 앱의 검색창에서 찾으려면 kakaotalk_ax_search_rooms를 사용하세요.
    """
    from kakaotalk_automation import NativeAX
    return output(NativeAX().rooms(query))


@mcp.tool(annotations=UI)
def kakaotalk_ax_search_rooms(query: str) -> str:
    """공식 앱의 채팅방 검색창에 query를 설정하고 AX에 노출된 방 목록을 반환합니다. 빈 문자열은 검색 해제."""
    from kakaotalk_automation import NativeAX
    return output(NativeAX().search_rooms(query))


@mcp.tool(annotations=UI)
def kakaotalk_ax_open(window_title: str, require_self: bool = False) -> str:
    """정확한 방 이름을 AX로 찾아 메뉴의 채팅방 열기를 실행합니다. 동명 방은 중단합니다.

    이미 열린 창은 다시 열지 않습니다. 목록에 없으면 앱의 방 검색을 사용합니다.
    require_self=True는 목록의 본인 방 배지가 확인된 경우만 허용합니다.
    별도 로그인·DB·마우스·CUA를 사용하지 않습니다.
    """
    from kakaotalk_automation import NativeAX
    return output(NativeAX().open_room(window_title, require_self))


@mcp.tool(annotations=UI)
def kakaotalk_ax_close(window_title: str) -> str:
    """정확한 대상 대화창만 닫습니다. 방 나가기·앱 종료가 아닙니다. 기존 초안이 있으면 보존하고 중단합니다."""
    from kakaotalk_automation import NativeAX
    return output(NativeAX().close_room(window_title))


@mcp.tool(annotations=UI)
def kakaotalk_ax_read_many(window_titles: list[str], limit: int = 50, open_if_needed: bool = True,
                          close_after: bool = False, max_workers: int = 3) -> str:
    """1~20개 방을 헬퍼 내부에서 열고 최대 4개 네이티브 프로세스로 병렬 조회합니다.

    방별 결과와 실패를 각각 반환합니다. 같은 시점의 원자적 스냅샷은 아닙니다.
    close_after=True일 때 이 호출이 새로 연 창만 닫습니다. 기존 창과 초안은 보존합니다.
    """
    from kakaotalk_automation import NativeAX
    return output(NativeAX().read_many(window_titles, limit, open_if_needed, close_after, max_workers))


@mcp.tool(annotations=NAVIGATE)
def kakaotalk_ax_load_more(window_title: str, pages: int = 1) -> str:
    """열린 방의 대화 스크롤 영역에서 이전 대화 로딩을 최대 10번 요청하고 다시 읽습니다.

    AX 스크롤바가 있으면 맨 위로 이동합니다. 추가 행 미노출을 전체 기록의 끝이라고 판단하지 마세요.
    steps의 AX 결과, contentChanged, historyComplete=false를 확인하세요.
    """
    from kakaotalk_automation import NativeAX
    return output(NativeAX().load_more(window_title, pages))


@mcp.tool(annotations=UI)
def kakaotalk_ax_search(window_title: str, query: str, limit: int = 100, load_pages: int = 0,
                        case_sensitive: bool = False, use_app_search: bool = True) -> str:
    """방을 열고 앱의 본문 검색을 실행한 뒤 AX에 노출된 본문 중 일치한 행을 반환합니다.

    load_pages=1..10이면 검색 전에 이전 대화 로딩을 요청합니다.
    use_app_search=False면 앱 검색창을 건드리지 않고 노출된 본문에서만 문자 그대로 검색합니다.
    검색 결과는 searchScope/coverage 범위에 한정됩니다. 전체 과거의 모든 일치 항목을 보장하지 않습니다.
    """
    from kakaotalk_automation import NativeAX
    return output(NativeAX().search_messages(window_title, query, limit, load_pages, case_sensitive, use_app_search))


@mcp.tool(annotations=UI)
def kakaotalk_ax_close_search(window_title: str) -> str:
    """열린 대화창의 검색 UI를 닫습니다. 메시지 입력창의 초안은 변경하지 않습니다."""
    from kakaotalk_automation import NativeAX
    return output(NativeAX().close_search(window_title))


@mcp.tool(annotations=SEND)
def kakaotalk_ax_send(window_title: str, text: str, operation_id: str, wait_seconds: float = 3,
                      require_self: bool = False) -> str:
    """AX로 방 열기·기준 본문 읽기·한 번 전송·새 본문과 빈 입력창 확인을 묶습니다. DB를 쓰지 않습니다.

    operation_id를 요청별 한 번 생성하고 재사용하세요. uncertain/타임아웃은 재발송하지 마세요.
    observed_in_ax는 새 텍스트 행과 빈 입력창을 관찰한 결과이며, 작성자·서버 ACK·상대방 읽음 확인이 아닙니다.
    같은 ID는 다시 보내지 않습니다. 결과 확인은 kakaotalk_ax_send_result를 사용하세요.
    """
    from kakaotalk_automation import NativeAX
    return output(NativeAX().send_message(window_title, text, operation_id, wait_seconds, require_self))


@mcp.tool(annotations=READ)
def kakaotalk_ax_send_result(operation_id: str, wait_seconds: float = 0) -> str:
    """AX 전송 기록을 조회하고 필요시 읽기만으로 결과를 재확인합니다. 창을 열거나 다시 보내지 않습니다."""
    from kakaotalk_automation import NativeAX
    return output(NativeAX().send_result(operation_id, wait_seconds))


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
