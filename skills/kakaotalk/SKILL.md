---
name: kakaotalk
description: "카카오톡(카톡)의 방 열기·닫기·본문 읽기·일괄 읽기·검색·이전 대화 요청·메시지 전송을 네이티브 AX 헬퍼로 수행한다."
---

# 카카오톡

앱 자동화는 기존 `kakaotalk_automation.NativeAX` 헬퍼와 `kakaotalk_ax_*` 도구를 사용한다.
모든 읽기·쓰기는 AX만 사용한다. CUA·스크린샷·OCR·좌표 클릭·키 입력·임시 스크립트·DB로 대체하지 않는다.
로그인이나 기기 등록은 하지 않는다.

## 실행

연결된 `kakaotalk_ax_*` MCP 도구를 사용한다. 연결이 없거나 스키마가 다르면 아래 클라이언트로
최신 MCP를 호출한다. `SKILL_DIR`는 이 스킬 폴더의 절대 경로로 바꾼다.

```bash
python3 SKILL_DIR/scripts/kakaotalk.py status
python3 SKILL_DIR/scripts/kakaotalk.py tools
python3 SKILL_DIR/scripts/kakaotalk.py call kakaotalk_ax_rooms --args-file /absolute/path/arguments.json
python3 SKILL_DIR/scripts/kakaotalk.py call kakaotalk_ax_send --args-file /absolute/path/request.json
```

클라이언트는 `~/Documents/workspace/kakaotalk-reader/.venv`를 사용한다. 체크아웃이 다르면
`--root` 또는 `KAKAOTALK_READER_ROOT`를 지정한다. 사용자 텍스트를 셸 코드에 넣지 않고 JSON 파일로 전달한다.

## AX 자동화

- 열린 창은 `kakaotalk_ax_windows`,
  방 목록은 `kakaotalk_ax_rooms`로 확인한다. 정확한 방 이름으로 `kakaotalk_ax_open`을 호출한다.
  앱의 방 검색은 `kakaotalk_ax_search_rooms`이며 빈 검색어는 해제다. 동명 방은 임의로 선택하지 않는다.
- 한 방은 `kakaotalk_ax_read`, 여러 방은 `kakaotalk_ax_read_many(window_titles=[...])`를 사용한다.
  일괄 도구가 창 열기와 병렬 읽기를 수행하므로 별도의 제어 코드나 스레드를 작성하지 않는다.
  `close_after=True`이면 그 호출이 새로 연 창만 닫는다. 창만 닫기는 `kakaotalk_ax_close`이며 초안은 보존한다.
- `items`에는 현재 AX에 노출된 행만 포함된다. `kind=text`의 `text`가 메시지 본문이고,
  나머지 행은 `unclassified`/`axParts`에 보존한다. 작성자와 생략된 시각은 추측하지 않는다.
- `hasMore=true`면 `nextOffset`/`snapshotId`를 다음 호출의 `offset`/`snapshot_id`로 전달한다.
  `snapshot_changed`면 첫 페이지부터 다시 읽는다. `hasMore=false`라도 전체 과거 기록을 다 읽었다는 뜻은 아니다.
- 이전 대화는 `kakaotalk_ax_load_more(window_title, pages=1)`로 제한해서 요청한다. 추가 행이 없다는
  결과만으로 전체 과거 기록의 끝이라고 판단하지 않는다. 최대 1000행이며 `coverage`와 실제 AX 결과를 확인한다.
- `kakaotalk_ax_search(window_title, query)`는 앱 검색을 실행하고 노출된 본문 중 일치한 항목을 반환한다.
  전체 검색 인덱스를 수집했다고 설명하지 않는다. `use_app_search=False`는 노출 본문만 검사한다.
  `kakaotalk_ax_close_search`로 검색 UI를 닫는다.
- AX 전송은 `kakaotalk_ax_send(window_title, text, operation_id)`다. 요청 ID를 한 번 정해서 보관한다.
  본인 방만 확인할 때는 `require_self=True`를 쓴다. 기존 초안은 지우지 않는다.
  `observed_in_ax`는 새 텍스트와 빈 입력창을 관찰한 결과이며 작성자·서버 ACK·읽음 확인이 아니다.
  타임아웃·`outcome_unknown`에서는 원래 ID로 `kakaotalk_ax_send_result`만 조회하고 새 ID로 재발송하지 않는다.
- MCP 연결이 없으면 CLI `kakaotalk-automation ax-call METHOD --args-file /absolute/path/arguments.json`을
  사용한다. 메서드·도구 대응과 정확한 인수는 [AX API](references/ax-api.md)를 읽는다.


## 제공 범위

v0.8.0부터 DB 조회·SQL·계정 키 복구·DB 기반 전송 확인·미디어 다운로드 경로는 제거되었다.
예전 도구 이름을 호출하거나 DB 폴백을 구성하지 않는다. `status`도 AX 창 상태만 확인한다.
최근 일주일처럼 기간 전체를 요청받아도 AX에 노출된 범위와 읽지 못한 범위를 구분한다.
사진·첨부·작성자·생략된 시각·전체 기록을 확인했다고 추측하지 않는다.
전송 본문은 사용자의 요청을 따른다. 본인 방에 검증을 요청받아 문구를 정해야 한다면
자연스러운 짧은 문장을 사용하고, 사용자가 원하지 않은 기술 설명이나 표식을 넣지 않는다.
