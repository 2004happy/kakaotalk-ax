# kakaotalk-ax

macOS 카카오톡을 네이티브 Accessibility(AX) API로 읽고 조작하는 CLI·Python 헬퍼·MCP 서버입니다.
방 열기·닫기, 본문 읽기, 여러 방 병렬 읽기, 검색, 이전 대화 요청, 메시지 전송을 제공합니다.

모든 기능은 실행 중인 공식 카카오톡 앱의 AX 요소를 사용합니다.
실행 명령은 `kax`이며, MCP 서버는 `kax mcp`로 실행합니다.

## 설치

macOS, Python 3.10 이상, 로그인된 공식 카카오톡 앱, Swift 컴파일러가 필요합니다.
macOS 설정에서 실행하는 터미널 또는 에이전트에 손쉬운 사용 권한을 부여해야 합니다.

```bash
xcode-select --install
python3 -m venv .venv
.venv/bin/pip install -e '.[mcp,dev]'
.venv/bin/kax build --kind read
.venv/bin/kax build --kind control
.venv/bin/kax build --kind send
.venv/bin/kax status
```

`status`는 열린 창의 AX 상태를 읽습니다.
네이티브 브리지는 소스 해시에 따라 로컬 캐시에 컴파일합니다. Python 기본 기능에는 추가 런타임 의존성이 없고,
MCP에는 `mcp>=1.30,<2`, 개발용 스킬 검증에는 PyYAML을 사용합니다.

## Python API

```python
from kakaotalk_automation import NativeAX

ax = NativeAX()
windows = ax.windows()
rooms = ax.rooms()
found = ax.search_rooms("방 이름")
opened = ax.open_room("정확한 방 이름")
page = ax.read_messages("정확한 방 이름", limit=50)
batch = ax.read_many(["방 A", "방 B"], close_after=True)
older = ax.load_more("정확한 방 이름", pages=1)
matching = ax.search_messages("정확한 방 이름", "검색어")
ax.close_search("정확한 방 이름")
ax.close_room("정확한 방 이름")
```

`read_messages`와 `windows`는 읽기 전용입니다. `rooms`는 채팅 탭을 선택할 수 있습니다.
방 열기·검색·이전 대화 요청 등은 앱 상태를 변경하며, 헬퍼가 AX 제어 동작을 직렬화합니다.
`read_many`는 필요한 창을 순서대로 열고 최대 4개 프로세스로 본문을 병렬 조회합니다.
`close_after=True`는 해당 호출이 새로 연 창만 닫으며 기존 창과 초안은 보존합니다.

자세한 인수와 도구 대응은 [AX API](skills/kakaotalk-ax/references/ax-api.md)를 참고하세요.

## 메시지 전송

명시적으로 요청받은 방과 본문에만 전송하세요. 요청별 `operation_id`를 한 번 정하고 보관합니다.

```python
result = ax.send_message(
    window_title="정확한 방 이름",
    text="요청받은 메시지",
    operation_id="request-unique-id",
    require_self=False,
)
# 결과가 불확실하면 원래 ID로 조회만 합니다.
result = ax.send_result("request-unique-id")
```

전송 헬퍼가 방 열기·기준 본문 조회·한 번의 AX 입력과 AXPress·결과 관찰을 묶습니다.
본인 채팅만 허용하려면 `require_self=True`를 지정합니다. 동명 방·기존 초안·확인할 수 없는 대상은 중단합니다.
전송 요청은 본문 해시와 함께 본인 전용 파일에 먼저 기록합니다. 같은 ID는 다시 보내지 않으며,
같은 ID를 다른 방이나 본문에 재사용하면 거부합니다.

`observed_in_ax`는 텍스트 행 증가와 빈 입력창을 관찰했다는 뜻입니다.
작성자·서버 ACK·상대방 읽음을 확인한 결과는 아닙니다. `outcome_unknown`, 응답 유실, 타임아웃에서는
새 ID로 재발송하지 말고 원래 ID로 `send_result`만 조회하세요. 불확실한 전송을 자동 재시도하지 않습니다.
파일 전송·메시지 수정·삭제는 제공하지 않습니다.

## CLI

```bash
.venv/bin/kax ax-windows
.venv/bin/kax ax-read --window-title '정확한 방 이름' --limit 50
.venv/bin/kax ax-call read_many --args-file /absolute/path/arguments.json
```

`ax-call`은 Python 메서드와 같은 이름과 JSON 인수를 사용합니다. 사용자 텍스트는 셸 코드 대신 JSON 파일로
전달하세요.
전송은 `ax-call send_message`, 결과 확인은 `ax-call send_result`입니다.

## MCP / 스킬

MCP 설정 예시입니다. 경로를 실제 체크아웃으로 바꾸세요.

```json
{
  "mcpServers": {
    "kakaotalk-ax": {
      "command": "/absolute/path/kakaotalk-ax/.venv/bin/kax",
      "args": ["mcp"]
    }
  }
}
```

| 기능 | 도구 |
|---|---|
| 열린 창 확인 | `kakaotalk_ax_windows` |
| 보이는 채팅방 목록 | `kakaotalk_ax_rooms` |
| 앱의 방 검색 | `kakaotalk_ax_search_rooms` |
| 방 열기 / 창 닫기 | `kakaotalk_ax_open` / `kakaotalk_ax_close` |
| 한 방 / 여러 방 읽기 | `kakaotalk_ax_read` / `kakaotalk_ax_read_many` |
| 이전 대화 요청 | `kakaotalk_ax_load_more` |
| 본문 검색 / 검색 닫기 | `kakaotalk_ax_search` / `kakaotalk_ax_close_search` |
| 전송 / 결과 확인 | `kakaotalk_ax_send` / `kakaotalk_ax_send_result` |

총 12개 AX 도구를 제공합니다.

저장소의 [카카오톡 스킬](skills/kakaotalk-ax/SKILL.md)은 이 AX 헬퍼를 사용합니다.
연결된 MCP가 없으면 스킬 클라이언트가 로컬 MCP 서버를 일회성으로 실행합니다.

```bash
.venv/bin/python skills/kakaotalk-ax/scripts/kax.py status
.venv/bin/python skills/kakaotalk-ax/scripts/kax.py tools
.venv/bin/python skills/kakaotalk-ax/scripts/kax.py call kakaotalk_ax_read --args-file /absolute/path/arguments.json
```

스킬의 `status`도 AX 창 상태만 읽습니다. 별도 체크아웃은 `--root` 또는 `KAKAOTALK_AX_ROOT`로 지정합니다.
스킬은 저장소에 포함되며 자동으로 개인 스킬 폴더에 설치하지 않습니다.

## 읽기 범위와 제한

- `rooms`는 현재 보이는 행을 읽습니다. `reportedRowCount`는 앱이 보고한 테이블 행 수이며 반환한 방 목록과 다릅니다.
- `read_messages`는 열린 창의 현재 AX 노출 행을 최대 1000행 읽습니다. `limit`은 1~500입니다.
  읽은 범위는 `coverage`에 표시하며, 계정 전체·기간 전체의 기록을 보장하지 않습니다.
- `hasMore`는 수집한 AX 스냅샷 안의 다음 페이지입니다. `nextOffset`과 `snapshotId`를 다음 호출에 전달하고,
  `snapshot_changed`면 첫 페이지부터 다시 읽으세요. `hasMore=false`도 과거 전체 기록의 끝을 뜻하지 않습니다.
- 본문 없는 행은 `unclassified`와 `axParts`로 보존합니다. 작성자·생략된 시각은 추측하지 않습니다.
- `load_more`는 AX 스크롤로 이전 대화를 요청합니다. 실제 로딩량은 앱이 결정하며 `historyComplete=false`,
  `endOfHistory=null`입니다. 본문 검색의 반환 항목도 그 시점 AX에 노출된 범위입니다.
- 여러 방 조회는 같은 시점의 원자적 스냅샷이 아닙니다. 방별 실패와 `partial`을 확인해야 합니다.
- 열린 창의 백그라운드·최소화 상태에서도 조회가 관찰되었지만, 앱이 AX 요소를 노출해야 합니다.
  닫힌 방은 먼저 열어야 하며 앱 버전이나 창 상태에 따라 접근성 구조가 달라질 수 있습니다.

## 검증

```bash
.venv/bin/python -m unittest discover -s tests -v
```

[별도 계정에서 헬퍼와 화면을 비교한 기록](benchmarks/2026-11-03-new-account-comparison.md)에
관찰한 범위와 한계를 적었습니다.

## 라이선스

[MIT](LICENSE). 카카오톡 이용약관을 지키는지, 다른 사람의 대화를 어떻게 다루는지는 쓰는 사람의 책임입니다.
