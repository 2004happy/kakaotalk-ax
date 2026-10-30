# GUI 아래 계층의 전송 경로 조사

2026-10-30, macOS의 KakaoTalk 26.6.1 (1190)에서 확인한 실험이다.
MCP에서 나와의 채팅으로 **한 건의 실제 전송과 서버 기록 조회에 성공**했다.
이후 기존 Mac 앱이 로그인 화면으로 돌아간 문제가 관측되어 **PC 방식의 전송과 재인증은 현재 차단**되어 있다.
0.3 MCP는 DB·미디어 조회 16개와 태블릿 전송 1개를 제공하며, 이전 PC 전송과 UI 경로는
도구 목록에서 제외했다. 기존 PC 전송 구현 자체도 계속 차단되어 있다.

별도 후속 경로로 `src/kakaotalk_tablet.py`와 일반 방 전송용 `kakaotalk_send`를 추가했다.
이는 Android 태블릿 보조기기로 따로 등록하는 경로이고 기존 Mac의 인증 파일·기기 ID를
사용하지 않는다. Android 익명 `GETCONF` status=0 응답은 실제 확인했으며,
익명 userId=0의 `CHECKIN`은 status=-203으로 거부됐다. 이는 본인 계정 인증 성공의
증거가 아니다. 본인 계정 등록은 사용자 터미널의 비밀번호 입력과 휴대폰 승인으로 진행했다.
후속 검증 결과는 아래와 같다.

## 태블릿 방식의 실제 공존·전송 검증 (2026-10-30)

- 초기 PC 소켓 감지가 0건이었던 이유는 `lsof`가 숫자 포트 대신 서비스명을 반환하는데 코드가
  숫자로 비교했기 때문이다. `-nP`로 수정하고 회귀 검사를 추가했다.
- 기존 태블릿 인증을 재사용한 `kakaotalk-tablet verify`가 본인 계정 접속에 성공했다.
  새 REST 로그인이나 인증 갱신 없이 12초간 기존 PC LOCO 소켓 한 개의 연속성을 확인했다.
- 19:04:17 KST, stdio MCP의 **일반 전송 도구** `kakaotalk_send(room_id, text)`로
  나와의 채팅에 `태블릿 MCP 전송 테스트입니다. PC 로그인 유지 확인용입니다.`에 시각을 붙여 한 건 전송했다.
- 서버 ACK status=0과 logId를 받았다. 그 logId의 정확한 본문과 본인 발신 정보가
  Mac 앱의 로컬 DB에서도 약 2초 이내에 확인됐다. 도구는 해당 DB를 읽기 전용으로만 열었으므로
  이는 Mac 앱이 서버에서 새 메시지를 받아 저장한 관측이다.
- 전송 전후 32.4초간 다섯 차례 관측에서 PC 프로세스의 기존 LOCO 소켓이 그대로 유지됐다.
  추가 인증 갱신, 앱 조작, AX, 컴퓨터 유즈는 사용하지 않았다. 실제 전송은 한 건이며 재시도하지 않았다.
- 자기 채팅으로 제한하지 않는 일반 방 ID 전송 코드를 사용했다. 1:1·단톡·오픈방의 대상 ID 전달은
  합성 테스트로만 검사했고, 해당 방들에는 실제 메시지를 보내지 않았다.
- 전체 51개 테스트 통과. 이 결과는 측정 구간의 동작을 확인하며 장기 안정성 전체를 증명하지는 않는다.

상세 절차와 검증 범위는 저장소 README에 기록했다.

## 인증·전송 실험의 최종 관측

- 현재 Mac과 일치하는 캐시 헤더를 찾았지만, 그 토큰의 `LOGINLIST`는 `-950`으로 거부됐다.
- 저장된 앱 자동 로그인 정보의 형식을 정적으로 확인하고, 같은 Mac에서 자동 로그인 요청을 했다.
  응답 status=0과 기존 로컬 DB의 계정 ID 일치를 확인했다. 비밀번호를 입력하거나 출력하지 않았다.
- `GETCONF → CHECKIN → LOGINLIST`에 성공했고, 서버의 로그인 계정 ID가 본인과 일치했다.
- 로컬 DB type=5와 서버 `MemoChat`을 대조한 뒤, 실제 stdio MCP 호출로
  `카카오톡 자동화 전송 테스트입니다.`를 한 번 `WRITE`했다. 서버 status=0과 logId를 받았다.
- 첫 후속 조회에서 `chatLogs=null`을 처리하지 못해 검증 오류가 났다. 재전송하지 않고
  읽기만 수행하여, `CHATONROOM`의 최신 logId 일치 및 `SYNCMSG`의 본문·발신자·logId 일치를 확인했다.
  실제 관측에서는 `cur=logId-1`이 빈 결과를, `cur=0`이 전송한 한 건을 반환했다.
- 사용자가 로그인 풀림을 보고했고 앱 화면도 실제 `로그인`으로 돌아가 있었다.
  자동 로그인 갱신과 `LOGINLIST`의 영향을 분리 검증하지 않아, 어떤 요청이 기존 인증을
  무효화했는지까지는 확정하지 못했다. 이 방식의 상시 사용 가능성을 입증한 것은 아니다.
- 진행 중인 LOCO 연결을 모두 닫았다. `send_self`, 직접 `Session.connect`, 자동 로그인 갱신
  스크립트를 기본 차단하고, 기본 상태에서 네트워크 연결이 발생하지 않는 회귀 검사를 추가했다.

발급받은 실험 세션은 저장소 밖 `~/.config/kakaotalk-reader/loco-session.json`에
권한 `0600`으로 저장되어 있다. 비밀번호는 저장하지 않았으며 현재 차단된 코드는 이 파일을
사용하여 로그인하지 않는다. 앱 자체의 plist·키체인·바이너리는 변경하지 않았다.

## 초기 익명 진단의 재현

저장소 루트에서 실행한다. Python 표준 라이브러리만 사용한다.

```sh
# 설치된 앱의 버전, 전송 관련 선택자 문자열, 서명 권한만 확인
python3 experiments/deep_send_probe.py

# 익명 GETCONF 1회 + 실행 중인 앱의 task port 접근 가능 여부 확인
python3 experiments/deep_send_probe.py --network --task-access

python3 -m unittest discover -s tests -p 'test_deep_send_probe.py' -v
```

`--network`는 `booking-loco.kakao.com:443`에 표준 TLS 인증서·호스트 이름 검증으로
접속한다. 유일한 요청은 `userId=0`인 익명 `GETCONF`다. 응답 본문은 1 MiB와
중첩 깊이 16으로 제한하고, 응답 헤더·BSON 길이·종료 문자를 검증한다. 출력에는
상태와 필드 이름만 포함하고 설정 본문 값은 제외한다.

`--task-access`는 Swift가 필요하다. `task_for_pid` 호출 후 포트를 얻으면 즉시
반납한다. 프로세스 메모리를 읽거나 쓰는 코드는 없다. 계정 설정, 키체인, 앱의
암호화된 인증 정보는 이 진단 코드에서 읽지 않는다.

## 실제 측정 결과

| 실험 | 관측 | 의미 |
| --- | --- | --- |
| LOCO booking 접속 | TLS 1.3, 헤더 status=0, BSON status=0, 본문 1,340 bytes | GUI 없이 실제 카카오 서버의 설정 응답을 받았다 |
| 앱 직접 접근 | `task_for_pid` 결과 5, 포트 획득 실패 | 이 환경에서는 실행 중인 앱의 내부 함수를 직접 호출하는 실험으로 진행하지 못했다 |
| 앱 권한 | `get-task-allow`, library-validation 비활성화, DYLD 환경 허용 모두 false | 일반적인 디버거·라이브러리 주입에 사용할 권한이 선언되어 있지 않다 |
| 전송 관련 선택자 문자열 | 네 항목 모두 존재 | 기능의 단서이며, 외부 호출 성공의 증거는 아니다 |
| 이 익명 진단의 로그인·전송 | 미실행, 전송 0건 | 별도 인증·전송 실험 결과는 문서 상단에 기록 |

네트워크 결과는 당시 서버 응답이며 추후 달라질 수 있다. 앱 접근 결과는 해당
실행 환경의 관측으로, 모든 macOS 환경에서의 불가능을 뜻하지 않는다.

## 추가 정적 분석

설치된 ARM64 바이너리의 Objective-C 메타데이터를 `xcrun dyld_info -arch arm64
-objc`로 확인하고, 임시 복사본의 해당 함수 구간을 `otool -tvV`로 분석했다.
설치된 앱 파일을 변경하거나 복사본을 실행하지 않았다. 주소는 이 버전 전용이다.

| 클래스/메서드 | 시작 주소 | 확인한 내용 |
| --- | --- | --- |
| `LPSchemeController.isAvailableLeverageSendTextSchemeURLWithParameters:userInfo:` | `0x1013dc690` | `message` 매개변수와 별도 `userInfo`의 `chatMessage` / `chatId`를 참조하고 `chatRoomWithId:`를 호출 |
| `LPSchemeController.leverageSendTextWithParameters:userInfo:` | `0x1013df860` | 같은 채팅 문맥을 사용하여 최종적으로 `sendTextMessage:extra:supplement:`를 호출 |
| `LPSchemeController.isAcceptableExternalTalkSchemeURL:` | `0x1013dac14` | 외부 URL 수용 조건을 별도로 검사. 팀채팅 검사 및 chatbot/chatgpt 분기가 존재 |

따라서 앱 내부에는 기존 로그인 세션을 이용하는 전송 경로가 존재한다. 다만
일반 외부 URL만으로 필요한 `userInfo`를 공급하여 이 경로에 도달하는 방법은
확인하지 못했다. 미확인 URL을 실제 채팅방과 본문을 넣어 실행하지 않았다.

별도 인증·전송 실험은 문서 상단과 `src/kakaotalk_loco.py`에 기록했다.
앱의 `mactalkAESDecrypt:`는 UUID의 SHA-256을 AES-256 키로 사용하고,
저장값 앞 16바이트를 IV로 사용하는 CBC/PKCS#7 형식이었다. 해석된 자동 로그인 키는
64바이트였으며 원문 비밀번호가 아니다. 이 형식은 설치된 버전에서만 확인했다.
기기 신규 등록은 수행하지 않았다.

## 참고한 공개 1차 자료

- [openkakao-cli의 LOCO 패킷 구현](https://github.com/JungHoonGhae/openkakao-cli/blob/main/src/loco/packet.rs)
- [연결·인증 클라이언트 구현](https://github.com/JungHoonGhae/openkakao-cli/blob/main/src/loco/client.rs)
- [2026-10-11에 병합된 수동 로그인·기기 등록 수정](https://github.com/JungHoonGhae/openkakao-cli/pull/60)

이 진단 코드는 패킷 구조를 바탕으로 독립적으로 작성했다. 공개 클라이언트의
인증 성공 여부는 이 계정의 성공 여부로 간주하지 않는다.
