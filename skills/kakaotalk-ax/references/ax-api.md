# AX 헬퍼 API

전체 API는 아래 12개 AX 도구다.

Python 진입점은 `from kakaotalk_automation import NativeAX; ax = NativeAX()`다.
Python 헬퍼가 패키지에 포함된 Swift AX 브리지를 호출한다.

| Python 메서드 | MCP 도구 |
|---|---|
| `windows()` | `kakaotalk_ax_windows` |
| `rooms(query="")` | `kakaotalk_ax_rooms` |
| `search_rooms(query)` | `kakaotalk_ax_search_rooms` |
| `open_room(window_title, require_self=False)` | `kakaotalk_ax_open` |
| `close_room(window_title)` | `kakaotalk_ax_close` |
| `read_messages(window_title, limit=50, offset=0, snapshot_id="")` | `kakaotalk_ax_read` |
| `read_many(window_titles, limit=50, open_if_needed=True, close_after=False, max_workers=3)` | `kakaotalk_ax_read_many` |
| `load_more(window_title, pages=1)` | `kakaotalk_ax_load_more` |
| `search_messages(window_title, query, limit=100, load_pages=0, case_sensitive=False, use_app_search=True)` | `kakaotalk_ax_search` |
| `close_search(window_title)` | `kakaotalk_ax_close_search` |
| `send_message(window_title, text, operation_id, wait_seconds=3, require_self=False)` | `kakaotalk_ax_send` |
| `send_result(operation_id, wait_seconds=0)` | `kakaotalk_ax_send_result` |

CLI는 `.venv/bin/kax ax-call METHOD --args-file FILE`이다.
인수 파일은 Python 메서드와 같은 키의 JSON 객체이며 `windows`/`rooms`는 생략할 수 있다.
스킬 클라이언트로는 `scripts/kax.py call MCP_TOOL --args-file FILE`을 사용한다.

일괄 조회 예시:

```json
{"window_titles":["방 A","방 B"],"limit":50,"close_after":true}
```

`read_many`는 일부 방이 실패해도 `items`에 방별 결과를 보존한다. `partial`이면 `reason`을 확인한다.
`close_after`는 그 호출이 새로 연 창에만 적용한다. 원래 열린 창과 작성 중인 초안은 보존한다.

`rooms`/`search_rooms`의 `items`는 현재 보이는 방이다. 앱 테이블의 전체 행 수는 `reportedRowCount`다.
두 값을 전체 방 목록으로 혼동하지 않는다. 앱 검색은 참여자 이름으로도 일치할 수 있다.
`windows`는 확인 가능한 창의 `minimized` 상태도 반환한다.

조회 한도 `limit`은 1~500행이다. `items`에는 본문 없는 행도 포함되므로 메시지 개수와 다르다.
한 번 수집하는 최대 범위는 AX 노출 1000행이다. 페이지는 `nextOffset`/`snapshotId`를 다음 호출의
`offset`/`snapshot_id`로 전달한다. `snapshot_changed`면 첫 페이지부터 다시 읽는다.

`load_more.pages`는 1~10, `search_messages.load_pages`는 0~10이다. `endOfHistory=null`,
`coverage.historyComplete=false`는 의도된 한계다. 스크롤이나 검색이 과거 전체의 수집을 보장하지 않는다.
앱 검색도 반환 항목은 그 시점 AX에 노출된 본문 범위다. UI 변경 동작은 직렬화하며 본문 읽기는 병렬화한다.

전송은 명시적으로 요청받은 방·본문에만 수행한다. `operation_id`는 1~128자의 영문·숫자·`_.:-`다.
`observed_in_ax`는 텍스트 행 증가와 빈 입력창의 관찰이며 작성자·서버 ACK·상대방 읽음을 보장하지 않는다.
응답 유실이나 `outcome_unknown`은 같은 ID로 결과만 확인한다. 다른 ID로 재발송하지 않는다.
초안·동명 방·확인할 수 없는 대상은 `blocked`다. `close_room`은 창 닫기이며 방 나가기·삭제가 아니다.
