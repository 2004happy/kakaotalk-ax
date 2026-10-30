# kakaotalk-reader 0.3

카카오톡 Mac의 로컬 대화 기록을 읽는 CLI + MCP 서버입니다. 별도 태블릿 보조기기 인증을
사용하는 전송 기능도 있으며, 현재 계정에서 PC 연결 유지와 나와의 채팅 실전송·Mac DB 동기화를 확인했습니다.
MCP와 카카오톡 스킬은 앱 조작·AX·컴퓨터 유즈를 사용하지 않습니다.

## 설치

macOS, Python 3.10 이상, Mac 카카오톡과 SQLCipher가 필요합니다.

```bash
brew install sqlcipher
python3 -m venv .venv
.venv/bin/pip install -e '.[mcp,send]'
.venv/bin/kakaotalk-reader setup
```

조회 CLI만 사용하면 `pip install -e .`, 조회 MCP만 쓰면 `pip install -e '.[mcp]'`로 설치할 수 있습니다.
DB 조회와 태블릿 전송에는 접근성 권한이 필요하지 않습니다. 기존 설치는 가상환경을 재생성하지 않고
같은 설치 명령으로 업데이트할 수 있으며, DB 설정과 태블릿 인증은 저장소 밖에 유지됩니다.

## 사용할 수 있는 정보

| 기능 | 읽는 내용 |
|---|---|
| 방 목록·검색 | 실제 방 이름, 개인 별칭, 오픈채팅 제목, 방 종류, 인원, 안 읽은 메시지 수 |
| 방 상세 | 확인 가능한 전체 참여자, 공지, 마지막 읽음·동기화 위치, 목록의 완전성 |
| 대화 기록 | 방 이름/ID, 기간, 페이지, 메시지 ID 전후 조회 |
| 본문 검색 | 일반 메시지, 카드의 긴 본문, 답장에 인용된 내용 |
| 메시지 상세 | 카드 본문·버튼 이름, 답장 맥락, 사진·앨범·파일 메타데이터 |
| 미디어 | 연결된 로컬 파일 또는 Kakao CDN 첨부파일 읽기, 지원되는 사진의 MCP 이미지 반환 |
| 활동 집계 | 기간별 방·메시지 수, 직접 보낸 수, 최초·최근 메시지 시각 |
| 기타 데이터 | 연락처 이름, 방 폴더, 공지, 북마크, 로컬 톡캘린더 |
| 고급 조회 | 읽기 전용 SELECT/CTE 및 조회용 PRAGMA |

## CLI 예시

```bash
.venv/bin/kakaotalk-reader status
.venv/bin/kakaotalk-reader rooms --query '팀방'
.venv/bin/kakaotalk-reader rooms --unread-only --limit 50 --offset 0
.venv/bin/kakaotalk-reader room --room-name '팀방'
.venv/bin/kakaotalk-reader read --room-name '팀방' --since last7d -n 100
.venv/bin/kakaotalk-reader search '회의' --room-name '팀방' --since last7d
.venv/bin/kakaotalk-reader recent --since 2026-10-01 --until 2026-10-07
.venv/bin/kakaotalk-reader activity --since last7d --until now
.venv/bin/kakaotalk-reader notices
.venv/bin/kakaotalk-reader folders
.venv/bin/kakaotalk-reader bookmarks
.venv/bin/kakaotalk-reader events --since today --until 2026-12-31
.venv/bin/kakaotalk-reader users --query '홍길동'
.venv/bin/kakaotalk-reader message ROOM_ID LOG_ID
.venv/bin/kakaotalk-reader media ROOM_ID LOG_ID
.venv/bin/kakaotalk-reader media ROOM_ID LOG_ID --index 0 --download
.venv/bin/kakaotalk-reader --format json recent -n 100 --offset 100
```

`ROOM_ID`, `LOG_ID`는 조회 결과의 `chatId`, `logId`로 바꿉니다. 방 이름이 여러 방과 일치하면 임의로 선택하지 않으므로 ID를 사용하세요.

날짜는 `YYYY-MM-DD`, 시간대가 포함된 ISO 시각, `today`, `yesterday`, `now`를 받습니다. `last7d`는 최근 168시간이고 `-7d`는 7일 전 자정입니다. 날짜만 지정한 `until`은 그 날짜 전체를 포함하며, ISO 시각은 정확한 종료 시각 전까지 조회합니다.

## MCP 연결

설치 후 `.venv/bin/kakaotalk-mcp`로 표준 입출력 서버를 실행할 수 있습니다.

Claude Desktop의 `claude_desktop_config.json` 예시:

```json
{
  "mcpServers": {
    "kakaotalk": {
      "command": "/absolute/path/to/kakaotalk-reader/.venv/bin/kakaotalk-mcp"
    }
  }
}
```

Claude Code에서는 다음처럼 등록합니다.

```bash
claude mcp add --transport stdio kakaotalk -- /absolute/path/to/kakaotalk-reader/.venv/bin/kakaotalk-mcp
```

### MCP 도구 17개 — 조회 16개, 전송 1개

- 상태: `kakaotalk_status`
- 방: `kakaotalk_rooms`, `kakaotalk_room`
- 대화: `kakaotalk_read`, `kakaotalk_recent`, `kakaotalk_search`, `kakaotalk_message`
- 연락처·집계: `kakaotalk_users`, `kakaotalk_activity`
- 공지·폴더·북마크·일정: `kakaotalk_notices`, `kakaotalk_folders`, `kakaotalk_bookmarks`, `kakaotalk_events`
- 첨부파일: `kakaotalk_media`, `kakaotalk_get_media`
- 고급 조회: `kakaotalk_sql`
- 태블릿 전송: `kakaotalk_send(room_id, text)`

0.3에서는 UI 조회 도구 두 개와 이전 PC 방식의 `kakaotalk_send_self`를 MCP에서 제거했습니다.
`kakaotalk_status`도 UI 인수를 받지 않습니다. DB에 없는 기록은 누락으로 표시하고 UI로 우회하지 않습니다.

0.2부터 목록 결과는 배열을 바로 반환하지 않고 `items`, `total`, `hasMore`, `nextOffset` 등을 담은 객체를 반환합니다. 기존 클라이언트는 `result.items`를 읽도록 변경해야 합니다. `hasMore=true`면 `nextOffset`으로 다음 페이지를 읽어야 전체 범위를 확인할 수 있습니다. 메시지는 최신 페이지부터 조회하되 각 페이지 안에서는 시간순으로 반환합니다.

대화 내용은 `text`를 읽으세요. `message`는 DB의 원래 미리보기이고, `text`는 카드의 긴 본문까지 합친 내용입니다. `reply`에는 인용한 메시지가, `media`에는 첨부파일 목록이 있습니다. 미디어 목록은 원문 다운로드 URL이나 인증 토큰을 노출하지 않습니다.

방 이름은 `NTChatMeta`의 개인 별칭, `NTChatRoom`의 제목, `NTOpenLink`의 오픈채팅 제목, 연락처 이름 순으로 찾습니다. 이 정보가 없으면 참여자 이름을 대신 표시하며 `nameSource=participants`, `nameExact=false`로 구분합니다. 참여자 목록도 `membersComplete`로 완전성을 표시합니다.

## 카카오톡 스킬

[skills/kakaotalk](skills/kakaotalk/SKILL.md)에 버전 관리되는 스킬과 MCP 클라이언트가 있습니다.
로컬 Codex 설치:

```bash
mkdir -p ~/.codex/skills/kakaotalk
cp -R skills/kakaotalk/. ~/.codex/skills/kakaotalk/
python3 ~/.codex/skills/kakaotalk/scripts/kakaotalk.py status
```

`$kakaotalk`으로 대화 조회·검색·요약·첨부파일 읽기·요청한 메시지 전송에 사용합니다.
연결된 MCP 도구가 없어도 스킬의 클라이언트가 프로젝트 가상환경에서 stdio MCP를 호출합니다.
체크아웃을 옮겼다면 `KAKAOTALK_READER_ROOT` 또는 클라이언트의 `--root`로 위치를 지정합니다.

```bash
python3 ~/.codex/skills/kakaotalk/scripts/kakaotalk.py tools
python3 ~/.codex/skills/kakaotalk/scripts/kakaotalk.py call kakaotalk_rooms --args-file /absolute/path/arguments.json
python3 ~/.codex/skills/kakaotalk/scripts/kakaotalk.py send --room-id ROOM_ID --text-file /absolute/path/message.txt
```

인수 JSON과 메시지 본문은 파일로 전달하므로 멀티라인·특수문자를 셸 코드에 삽입할 필요가 없습니다.
이미지는 원문 base64 대신 `imageFiles`의 본인 전용 로컬 파일로 반환합니다. 전송 응답이 불명확하면
자동 재시도하지 않으며, 이 클라이언트가 로그인이나 PC 재인증을 자동으로 시작하지 않습니다.

## 읽기 범위와 동작

- 로컬 DB를 SQLCipher `-readonly -safe -noinit`으로 엽니다. 파일 입출력 함수, shell 명령, DB 변경·추가 연결은 조회 API에서 거부합니다.
- 대화 텍스트 조회는 카카오 서버에 접속하지 않습니다. 명시적인 `get_media`/`media --download` 요청만 저장된 `https://*.kakaocdn.net` 주소를 사용하며, 외부 도메인으로의 리디렉션을 거부합니다.
- 미디어는 최대 25MB, 인라인 이미지는 최대 8MB입니다. 내려받은 파일은 `~/.cache/kakaotalk-reader/media/`에 본인만 읽을 수 있게 저장하며 이후에는 캐시를 사용할 수 있습니다. 만료된 URL에 캐시도 없으면 `media_unavailable`을 반환합니다.
- Mac에 아직 동기화되지 않은 모바일 대화는 포함되지 않을 수 있습니다. 사진·영상 내용은 텍스트 DB만으로 해석하지 않으며, 사진은 미디어 도구로 별도 조회합니다.
- 톡캘린더는 로컬에 저장된 일정만 조회하고 반복 일정의 개별 발생일은 자동 전개하지 않습니다. 전체 텍스트 검색은 한 번에 2만 건까지 검사하며, 초과하면 방/기간을 좁히도록 안내합니다.

## 계정 자동 탐지

기존 plist의 숫자 계정 정보 또는 최근 버전의 계정 SHA-512 값을 이용하고, 계산한 DB 파일명과 일치하는지 검증합니다. SHA-512 값이 있으면 기본 범위는 1~60억, 없으면 기존 파일명 방식으로 1~200만입니다. 최대 4개 프로세스와 기본 60초 제한을 사용합니다.

```bash
.venv/bin/kakaotalk-reader setup --timeout 180
.venv/bin/kakaotalk-reader setup --max-id 6000000000 --timeout 180
```

설정은 `~/.config/kakaotalk-reader/config.json`에 권한 `0600`으로 원자적으로 저장하며, 복호화 키 자체는 저장하지 않습니다.

## 전송 방식과 검증

별도 [실험 코드와 측정 기록](experiments/README.md)에 앱 내부 전송 경로 분석,
프로세스 접근 검사와 LOCO 인증·전송 결과를 정리했습니다. MCP에서 나와의 채팅에
한 건을 전송하고 서버 기록까지 확인했으나, 기존 Mac 앱이 로그인 화면으로 돌아가는
문제가 관측되어 **이전 PC 방식**의 전송과 재인증을 차단했습니다. 이 경로는 현재 MCP에
노출하지 않습니다. 제품의 전송 도구는 아래의 별도 태블릿 방식입니다.

### 태블릿 보조기기 전송

사용자 요구는 기존 Mac 앱 로그인 유지, 컴퓨터 유즈·AX·앱 조작 제외, 자기 채팅을 넘어
기존 1:1·단톡방 ID로 전송하는 것입니다. 새 `kakaotalk_send(room_id, text)`는 이 목적의
태블릿 경로이며, Mac 인증·기기 ID·세션 파일을 재사용하지 않고 PC 로그인으로 대체하지 않습니다.

```bash
.venv/bin/pip install -e '.[mcp,send]'
.venv/bin/kakaotalk-tablet login
.venv/bin/kakaotalk-tablet verify
.venv/bin/kakaotalk-tablet status
```

`login`은 사용자 터미널에서만 비밀번호를 가려서 입력받으며 저장하지 않습니다. 최초에는
휴대폰 카카오톡에서 기기 등록 코드 승인이 필요합니다. `--replace-tablet`는 기존 태블릿
자리를 교체하도록 요청하는 옵션이며 PC 프로필을 선택하는 옵션은 없습니다. 입력이 없는
비대화형 실행은 `user_input_required`를 반환하고 서버에 접속하지 않습니다.

등록 후 또는 `verify`로 본인 계정의 Android LOCO 로그인과 기존 Mac 프로세스의 LOCO 소켓 연속성을
12초간 확인합니다. 소켓 정보는 간접 관측이므로 앱 화면의 로그인 상태를 증명하지는 않습니다.
연속성을 확인할 수 없으면 발송을 막고 `coexistence_verification_required`를 반환합니다.
이 경우 앱이 계속 로그인 상태인지 사용자가 확인한 후 다음을 실행할 수 있습니다.

```bash
.venv/bin/kakaotalk-tablet confirm-desktop
```

등록된 태블릿 세션만 `~/.config/kakaotalk-reader/tablet-session.json`에 `0600`으로 저장합니다.
MCP 호출에는 방 ID와 본문을 지정하며, 결과가 `outcome_unknown`이면 중복 방지를 위해 재시도하지
않아야 합니다.

2026-10-30 실제 검증: 태블릿 등록·본인 계정 로그인에 성공했고, 12초간 기존 PC 연결 유지 후
stdio MCP의 `kakaotalk_send`로 나와의 채팅에 한 건을 전송했습니다. 서버 ACK의 logId와 같은
발신자·본문이 Mac 앱의 읽기 전용 DB에서도 확인됐으며 전송 전후 32.4초간 기존 PC LOCO 연결이
그대로 유지됐습니다. 앱 조작·AX는 사용하지 않았습니다. 일반 1:1·단톡·오픈방 ID의 전송 경로는
합성 테스트로 확인했으며, 해당 방들에 실제 메시지를 보내는 검증은 아직 하지 않았습니다.
이는 관측 구간의 공존·전송 확인으로, 모든 향후 연결 상황을 보장하는 장기 시험은 아닙니다.

프로토콜 형식과 태블릿 접속 방식의 1차 자료:
[Agent Messenger 문서](https://agent-messenger.dev/docs/cli/kakaotalk),
[Android 인증 구현](https://github.com/agent-messenger/agent-messenger/blob/main/src/platforms/kakaotalk/auth/kakao-login.ts),
[태블릿 프로필 구현](https://github.com/agent-messenger/agent-messenger/blob/main/src/platforms/kakaotalk/protocol/config.ts).

## 검증

```bash
.venv/bin/pip install -e '.[mcp,send,dev]'
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/kakaotalk
```

합성 DB와 모의 연결로 이름 출처, 동명 방, 참여자·페이지·시간 범위, 카드·답장 검색,
SQL 읽기 전용, 미디어 제한과 캐시 권한, 태블릿 프로필 분리, 방 ID 전송과 불명확한 결과의
재시도 방지, MCP 도구 범위, 스킬 클라이언트를 검증합니다. 테스트는 실제 메시지를 보내지 않습니다.

## 참고

- [Mac 키 생성 원리](https://gist.github.com/blluv/8418e3ef4f4aa86004657ea524f2de14)
- [Mac 복호화 연구](https://koreascience.kr/article/JAKO202331137843699.page)
- [최근 버전의 계정 SHA-512 값 설명](https://github.com/cskwork/kakao-userid-recover/blob/main/docs/how-it-works.md)
