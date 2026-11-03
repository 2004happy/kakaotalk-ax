# AX 전용 읽기 검증 — 2026-11-03

## 구현 범위

- Python: `NativeAX.windows()`, `NativeAX.read_messages(window_title, limit=50, offset=0, snapshot_id="")`.
- CLI: `kakaotalk-automation ax-windows`, `ax-read --window-title TITLE`.
- MCP: `kakaotalk_ax_windows`, `kakaotalk_ax_read`. 총 20개 도구 중 19개 조회, 1개 전송.
- 기존 `SendKakao.swift`는 변경하지 않고 별도의 읽기 전용 `ReadKakaoAX.swift`를 같은 헬퍼에 추가했다.
- 읽기 경로는 DB 리더를 실행하지 않고, 대상 창이나 입력창의 값을 변경하거나 AX 액션을 호출하지 않는다.

## 실제 앱 확인

이전에 메시지 10개를 보낸 시험 계정의 나와의 대화에서 검증했다. 테스트 시작 시 닫혀 있던 방은
개발용 네이티브 AX 코드로 `badge me`가 있는 셀을 유일하게 찾고 `AXShowMenu` →
`openChatRoom:`의 `AXPress`로 열었다. 이 준비 과정은 읽기 API에 포함되지 않는다.
읽기 API 자체는 창 열기·올리기·선택·스크롤을 수행하지 않는다.

검증 프로세스와 자식 프로세스에는 macOS sandbox를 적용하여 다음을 차단했다.

- 카카오톡의 실제 앱 컨테이너 아래 파일 읽기·쓰기. 디렉터리 접근의 `PermissionError`로 확인.
- 프로세스의 직접 네트워크 접근. 루프백 소켓 바인드의 `PermissionError`로 확인.

카카오톡 앱 자체는 이 sandbox 밖에서 평상시대로 동작했다. 앱 자체의 네트워크 통신을 차단하거나
측정했다는 의미는 아니다. 테스트 중 새 메시지는 보내지 않았고 별도 로그인도 하지 않았다.

| 확인 항목 | 결과 |
|---|---|
| 본문 10개 | 이전 시험 기록의 10개와 본문·순서 정확히 일치 |
| AX 행 | 12개: 본문이 있는 10행 + 본문 없는 미분류 2행 |
| DB 호출 | 0, 실제 앱 컨테이너 접근도 OS에서 차단 |
| 페이지 | 4행씩 3페이지를 합친 결과가 전체 읽기와 정확히 일치, 행 중복 없음 |
| 다른 snapshot ID | `snapshot_changed`, 빈 `items` 반환 |
| 닫힌 대상 창 | `target_room_window_not_open` |
| 메인 창을 대화로 읽기 | `chat_transcript_not_unique` |
| MCP stdio 경로 | 같은 10개 본문·순서 일치, DB 호출 0 |

최종 코드의 한 번의 warm 호출은 Python `read_messages` 전체 80.0ms,
Swift 브리지 내부 69ms였다. 컴파일과 MCP 서버 시작은 포함하지 않는다.
반복 측정한 지연 분포나 다른 도구와의 비교 수치는 아니며 일반적인 속도를 보장하지 않는다.

앱은 빈 `AXDescription`에도 `AXError.failure`를 반환했다. 최종 읽기에서는 해당 선택적 속성 실패
10개를 `coverage.optionalAttributeFailures`에 표시했다. 본문 `AXValue` 읽기의 오류는 성공으로
취급하지 않는다. 메시지 본문 10개는 별도로 정확히 일치하는지 확인했다.

## 한계

- `items`/`total`/`limit`은 메시지 개수가 아닌 AX 행 기준이다. 본문 없는 행도 미분류로 보존한다.
- 현재 앱이 AX에 노출한 최대 1000행만 읽는다. 과거 기록을 불러오지 않으며 `historyComplete=false`다.
- 작성자 판별은 아직 제공하지 않는다. 생략된 시각은 추정하지 않는다. 이 시험에서는 2개 메시지에서만
  날짜·시각이 노출됐고, 나머지는 null로 남겼다.
- 사진·이모티콘·답장 구조는 해석하지 않는다. 원래 AX 텍스트 속성은 `axParts`에 남긴다.
- 페이지 ID로 행·본문 변경을 검사하고 한 호출의 앞뒤 행 목록도 대조하지만, 앱 내부의 원자적
  스냅샷을 제공하지는 않는다. 실제 앱 검증은 이 나와의 대화에 한정된다.
- 기능 검증이며 계정 제재 가능성이나 장기 안정성에 대한 검증은 아니다.

## 코드 검증

- 단위 테스트 56개 통과. 기존 DB 조회·전송 테스트와 새 AX 파싱·페이지·DB 미사용 테스트 포함.
- 스킬 검증, `pip check`, `git diff --check` 통과.
- 깨끗한 임시 소스 복사본으로 wheel을 빌드해 두 Swift 소스의 포함과 원문 일치를 확인했다.
  삭제한 LOCO·태블릿·이전 UI 모듈은 포함되지 않았다.
- 로컬 가상환경의 editable 설치를 0.6.0으로 갱신했다.
- 실제 대화 본문과 계정 식별자는 이 기록이나 테스트 픽스처에 저장하지 않았다.
