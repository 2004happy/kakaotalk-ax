---
name: kakaotalk
description: "카카오톡(카톡) 대화 조회·검색·요약, 채팅방과 첨부파일 확인, 사용자가 요청한 기존 방 메시지 전송. 로컬 DB 조회와 별도 태블릿 LOCO 전송을 사용하며 컴퓨터 유즈·AX·앱 조작은 제외한다."
---

# 카카오톡

로컬 Mac 카카오톡 DB로 읽고, 별도로 등록한 Android 태블릿 LOCO 세션으로 보낸다.
PC 앱의 인증을 갱신하거나 PC 프로필로 새 로그인하지 않는다. 컴퓨터 유즈, AX,
AppleScript, 키 입력·마우스·클립보드를 전송 경로로 사용하지 않는다.

## 실행

필요한 `kakaotalk_*` MCP 도구가 연결되어 있으면 직접 사용한다. 도구가 없거나 기존 연결의
스키마가 다르면 이 스킬의 클라이언트로 최신 MCP 서버를 호출한다.
`SKILL_DIR`는 지금 읽은 스킬 폴더의 절대 경로로 바꾼다.

```bash
python3 SKILL_DIR/scripts/kakaotalk.py status
python3 SKILL_DIR/scripts/kakaotalk.py tools
python3 SKILL_DIR/scripts/kakaotalk.py call kakaotalk_rooms --args-file /absolute/path/arguments.json
python3 SKILL_DIR/scripts/kakaotalk.py send --room-id ROOM_ID --text-file /absolute/path/message.txt
```

클라이언트는 `~/Documents/workspace/kakaotalk-reader/.venv`를 자동 사용한다.
체크아웃 위치가 달라졌을 때만 `--root` 또는 `KAKAOTALK_READER_ROOT`로 바꾼다.
인수 JSON과 보낼 본문은 파일로 전달한다. 사용자 텍스트를 셸 코드에 보간하지 않는다.
미디어 응답의 이미지 파일은 `imageFiles`의 절대 경로로 반환하므로 필요하면 이미지 도구로 연다.

## 조회·요약

- 먼저 `kakaotalk_status`로 DB 범위와 태블릿 준비 상태를 확인한다. 이 호출은 새 로그인을 하지 않는다.
- 방은 `kakaotalk_rooms(query=...)`로 찾고 `chatId`를 사용한다. 동명이거나 부분 일치가 여러 개면 임의로 고르지 않는다.
  `nameSource`/`nameExact`, `membersComplete`로 이름과 참여자 정보의 근거를 구분한다.
- `kakaotalk_read`는 특정 방, `kakaotalk_recent`는 전체 방, `kakaotalk_search`는 본문·카드·답장 검색이다.
  목록은 `items`, `hasMore`, `nextOffset` 형식이다. 전체 범위를 요청받으면 필요한 페이지를 끝까지 읽는다.
- 최근 일주일은 `since="last7d"`, `until="now"`를 쓴다. `-7d`는 7일 전 자정으로 의미가 다르다.
  `text`가 카드 전체 본문을 포함한다. `message`만 읽으면 긴 내용을 놓칠 수 있다.
- 사진·파일은 `kakaotalk_media`로 목록을 확인한 뒤 `kakaotalk_get_media(room_id, log_id, index)`로 읽는다.
  만료된 원격 첨부파일은 `media_unavailable`로 설명한다. 토큰·원문 다운로드 URL을 꺼내 출력하지 않는다.
- 공지·북마크·폴더·일정·활동 집계는 각각 대응하는 도구를 쓴다. 상세 인수는 `tools`의 실제 스키마로 확인한다.
- DB에 없는 모바일 기록을 읽었다고 주장하지 않는다. 누락을 메우려고 UI 자동화로 우회하지 않는다.

예: 방 검색 인수 파일은 `{"query":"팀방","limit":100,"offset":0}`,
기간 조회는 `{"room_id":123,"since":"last7d","until":"now","limit":100,"offset":0}`이다.
실제 ID로 바꾸고, 기존 요청과 무관한 방을 추가 탐색하지 않는다.

## 메시지 전송

1. 사용자의 요청에서 수신 방과 보낼 내용을 확정한다. 이미 명확히 허용된 일상 메시지는
   다시 허락을 묻지 않고 처리한다. 불명확한 대상은 먼저 확인한다. 예전 테스트 요청을 새 전송의 허가로 사용하지 않는다.
2. `kakaotalk_rooms`/`kakaotalk_room`으로 실제 방 ID를 확인한다. 나와의 채팅은 type=5로 찾는다.
   전송 도구는 자기 채팅 전용이 아니며 기존 1:1·단톡·오픈방 ID를 받는다.
3. `kakaotalk_status.tablet.status`가 `ready`인지 확인한다. `kakaotalk_send(room_id, text)`를 **한 번** 호출한다.
   파일 전송이나 메시지 수정·삭제는 구현되지 않았으므로 다른 기능처럼 가장하지 않는다.
4. `sent`와 서버 ACK를 확인한다. 필요하면 반환된 `logId`로 `kakaotalk_message`를 조회해
   본인 발신·본문·ID가 같은지 확인한다. DB 반영이 늦으면 읽기만 몇 차례 다시 확인한다.
   서버 ACK와 Mac DB 동기화는 구분해서 보고한다.

`outcome_unknown`, `accepted_unverified`, 전송 호출 중의 타임아웃·연결 종료는 **재발송하지 않는다**.
먼저 기존 메시지 기록을 조회한다. 실패처럼 보인다는 이유로 같은 내용을 다시 보내지 않는다.
기존 PC 로그인 풀림이 관측되면 쓰기를 멈추고 그 상태를 알린다. 자동 PC 재인증으로 복구하려 하지 않는다.

## 인증·운영

프로젝트의 `.venv/bin/kakaotalk-tablet status`는 로컬 준비 상태,
`verify`는 저장된 **태블릿** 인증으로 접속하고 PC 소켓 연속성을 확인한다.
상태가 `ready`라는 것만으로 현재 토큰 유효성이나 장기 안정성을 단정하지 않는다.

인증이 없거나 `LOGINLIST_status_-950`이면 사용자 터미널에서 한 번 다음을 실행해야 한다.

```bash
~/Documents/workspace/kakaotalk-reader/.venv/bin/kakaotalk-tablet login
```

비밀번호는 채팅으로 받거나 저장하지 않는다. 최초 등록의 휴대폰 승인은 사용자가 진행한다.
`--replace-tablet`는 기존 태블릿 자리를 교체할 수 있으므로 그 교체를 허용받은 경우에만 사용한다.
`verification_required`이면 먼저 `verify`를 실행한다. 소켓으로 확인할 수 없을 때만 사용자에게
PC 앱의 로그인 유지 여부를 확인받고 `confirm-desktop`을 사용한다.
기존 `loco-session.json`·Mac 자동 로그인 키·PC 로그인 실험으로 우회하지 않는다.

구현과 상세 검증 기록은 프로젝트의 `README.md`, `experiments/README.md`에 있다.
2026-10-30의 실전송 검증은 나와의 채팅 한 건과 전후 32.4초의 PC 연결 유지·Mac DB 동기화다.
일반 방 ID 전달은 테스트했지만 해당 날짜에 다른 사람의 방으로 실제 테스트 메시지를 보내지는 않았다.
