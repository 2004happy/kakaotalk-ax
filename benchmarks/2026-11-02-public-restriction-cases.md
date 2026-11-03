# 카카오톡 자동화 관련 공개 이용제한 사례 조사

조사일: 2026-11-02. 보고일은 한국 시간이며 실제 제한 적용일과 다를 수 있다.
당사자·개발자가 직접 쓴 원문을 우선했다. 당사자의 피해 보고를 확인한 것과 카카오의
제재 원인을 독립적으로 입증한 것은 구분한다.

## 인용문의 출처와 기술적 분류

사용자가 질문한 비공식 클라이언트의 서버 접속에 관한 문장은 **kmsg 이슈가 아니라
[OpenKakao #13의 LOCO 관련 개발자 댓글](https://github.com/JungHoonGhae/openkakao-cli/issues/13#issuecomment-4173654845)**에서 나온다.
같은 댓글은 로컬 DB 읽기와 접근성 조작을 대안으로 소개한다.
따라서 그 문장이나 LOCO 피해 보고를 AX·DB 단독 사용의 제재 근거로 전용하지 않는다.

| 방식 | 기술적 분류 |
|---|---|
| 별도 인증·LOCO/REST 접속 | 도구가 자체적으로 카카오 서버와 통신하는 비공식 서버 클라이언트 |
| 공식 앱을 AX로 제어 | OS 접근성 API를 사용하는 자동화 보조 도구. 카카오 서버와 통신하는 주체는 공식 앱 |
| 로컬 DB 조회 | 로컬 파일 분석 도구. 해당 조회 경로는 서버 클라이언트가 아님 |

이 구분은 네트워크 경로에 대한 설명이다. 카카오가 해당 사용을 허용하는지에 대한
법률·약관 판단까지 확정한 것이 아니다.
[kmsg 설계 문서](https://github.com/channprj/kmsg/blob/main/ARCHITECTURE.md#accessibility-instead-of-a-private-protocol)도
공식 앱 UI 제어와 사설 프로토콜 접속을 구분한다.

## 확인한 직접 경험담

| 보고일 | 경로 | 직접 보고된 결과 | 원문 |
|---|---|---|---|
| 2026-04-02 | OpenKakao 비공식 LOCO 쓰기 | 개발자가 시험한 두 계정 중 한 계정이 삭제됐다고 보고. 해당 계정은 LOCO 쓰기 작업을 수행했으며 정확한 유발 행위는 모른다고 명시. | [개발자 댓글](https://github.com/JungHoonGhae/openkakao-cli/issues/13#issuecomment-4173654845) |
| 2026-06-28 | OpenKakao 비공식 새 기기 로그인·인증 재시도 | 사용자가 반복 시도 뒤 보조기기 로그인이 차단됐다고 보고. 인증 요청의 404 응답과 재시도 경로를 기재. | [사용자 댓글](https://github.com/JungHoonGhae/openkakao-cli/issues/20#issuecomment-4819842990) |
| 2026-08-07 이후 | AmpleReVanced 변조 Android 앱 | 여러 사용자가 계정 제한을 보고. 한 사용자는 영구 제한이라고 설명했고, 다른 사용자는 학교 공지 오픈방에서 발신하지 않았다고 설명. | [원 이슈](https://github.com/AmpleReVanced/revanced-patches/issues/158), [발신하지 않았다는 보고](https://github.com/AmpleReVanced/revanced-patches/issues/158#issuecomment-5230168848) |

### 1. LOCO 쓰기 계정 삭제 보고

OpenKakao 개발자는 이 경험 후 v1.1.0부터 LOCO 쓰기 명령을 기본 비활성화했다고 설명한다.
같은 댓글에서 대안으로 로컬 DB+Mac 접근성 도구를 소개하지만, 이는 개발자의 위험도 판단이다.
그 대안이 제재 없이 지속 작동한다는 대조 실험은 제시하지 않는다.
두 계정 중 한 계정이라는 수치를 일반적인 삭제 확률로 환산하지 않는다.

### 2. 비공식 로그인 뒤 보조기기 차단 보고

유지관리자는 잘못된 인증 경로를 배포한 점을 인정하고 해당 흐름을 되돌렸다고 답했다.
[유지관리자 답변](https://github.com/JungHoonGhae/openkakao-cli/issues/20#issuecomment-4828683399)

같은 사용자 `zerowoosik`이 [#22](https://github.com/JungHoonGhae/openkakao-cli/issues/22)에서도
유사 경험을 설명한다. #20과 #22를 별개의 피해자로 중복 집계하지 않는다.
이는 비공식 로그인·기기 등록 경로 사례이며, DB 단독 조회나 AX 전송의 피해 사례가 아니다.

### 3. 변조 앱의 계정 제한 보고

제보자 중 한 명은 고객센터가 API 오용을 이유로 안내했다고 전했으나 구체적인 API는 모른다고 밝혔다.
이는 제보자가 전달한 답변이며 고객센터 원문을 직접 검증한 것은 아니다.
[제보자의 후속 댓글](https://github.com/AmpleReVanced/revanced-patches/issues/158#issuecomment-5261970993)

유지관리자는 비패치 사용자에게도 비슷한 제한이 있었다며 이번 제한과 패치의 인과관계는
확정하지 않았다. [후속 사례 수집](https://github.com/AmpleReVanced/revanced-patches/discussions/162)

유지관리자가 공유한 자발적 설문 107응답 중 20제한·87비제한도 무작위 표본이나 대조군이
아니므로 일반적인 제재율로 쓰지 않는다.
[설문 언급](https://github.com/AmpleReVanced/revanced-patches/issues/158#issuecomment-5236456126)

[Reddit의 유사 피해 글](https://www.reddit.com/r/MorpheApp/comments/1vhydmd/be_careful_when_using_patched_apk_of_kakaotalk/)
도 확인했다. 계정 사용 기간·패치·시기가 겹치고 작성자의 동일인 여부를 알 수 없어 독립 피해 사례로
추가 집계하지 않았다. 해당 글의 재가입 제한 기간 주장도 공식 확인 없이 일반화하지 않는다.

이 사례는 앱 바이너리를 변조하는 Android 환경이다. 순정 Mac 앱의 로컬 DB 조회·AX 조작에
직접 대입할 수 없다. 공지 오픈방에서 보내지 않았다는 보고도 계정 전체의 발송량을 입증하지는
않으므로, 발송량의 영향이나 순정 앱 AX 동작의 위험도를 입증하는 자료로 사용하지 않는다.

## 보조 사례: 이미 차단된 기기의 로그인 오류

2021-03-03 [node-kakao #174](https://github.com/storycraft/node-kakao/issues/174)는 로그인 중
`-328` 응답과 보호조치를 보고했다. 댓글에서는 차단된 보조기기에서 로그인할 때의 응답이라고
설명한다. 로그인 시도가 새 차단을 발생시켰는지, 기존 차단 상태가 응답에 드러난 것인지는 구분되지 않는다.
오래된 비공식 프로토콜 사례이며 현재의 Mac 앱 동작으로 일반화하지 않는다.

## 이번 구현과 직접 비교할 수 있는 사례는 찾았는가

| 경로 | 조사 결과 |
|---|---|
| 공식 Mac 앱에서 로컬 DB 읽기만 사용 | 그 경로만 사용했다고 분리해서 기재한 직접적인 제재 보고를 찾지 못함. |
| 공식 Mac 앱에서 macOS AX만 사용 | 그 경로만 사용했다고 분리해서 기재한 직접적인 제재 보고를 찾지 못함. |
| 로컬 DB+AX 조합 | 우리와 유사한 프로젝트는 있으나, 확인한 공개 이슈에서 이 조합의 제재 원인을 분리한 사례는 찾지 못함. |

일반 웹 검색과 GitHub 공개 이슈 검색을 사용했다. `silver-flight-group/kakaocli`,
`channprj/kmsg`의 조회된 공개 이슈 목록, `JungHoonGhae/openkakao-cli`의 관련 이슈와 댓글,
`storycraft/node-kakao`, `dolidolih/Iris`, 관련 MCP 저장소의 제재 키워드를 확인했다.
검색어는 한·영의 정지·영정·차단·이용제한·보호조치·ban·banned·suspended 및
AXPress·Accessibility·SQLCipher·database·매크로 조합을 사용했다.

검색의 무결과는 사례의 부재나 안전성의 증거가 아니다. 닫힌 커뮤니티, 비공개 제보,
미색인 자료는 포함하지 못한다. 반대로 README의 위험 경고나 제재 가능성 질문은 실제 피해로 세지 않는다.

접근성 전송의 멈춤·오류도 계정 제재와 구별했다.
[kakaocli #9](https://github.com/silver-flight-group/kakaocli/issues/9)는 전송 명령이 멈추는
문제이며 계정 제재 보고로 세지 않았다. DB 키 파생 실패도 같은 이유로 제재에서 제외했다.

## 이번 사건에 대한 판단 갱신

공개 사례가 강하게 뒷받침하는 것은 **비공식 로그인·LOCO에서 실제 피해 보고가 존재한다는 점**이다.
사용자가 확인한 두 번째 제한은 LOCO 삭제 후 DB+AX 테스트 뒤 발생했으므로, 그 외부 사례로
이번 재발까지 LOCO 원인이라고 결론내리지 않는다.

앞선 AX 우선 조사 판단은 앱에 전달되는 동작 경로에 따른 기술적 추정이었다.
**이번 공개 사례 조사로 AX가 DB보다 제재될 확률이 높다는 주장을 뒷받침하지는 못했다.**
DB 단독과 AX 단독의 분리 검증 필요성이 유지된다.
외부 프로젝트의 무위험·서버 접촉 없음이라는 문구도 계정 안전성을 보장하는 근거로 사용하지 않는다.

연구 중 실제 계정의 조회·발송·로그인, 앱 설정 변경은 하지 않았다.

## 추가 커뮤니티 조사 — 2026-11-02 후속

앞선 조사는 GitHub 중심이었고 한국 커뮤니티까지 충분히 살펴본 조사로 설명할 수 없었다.
이번에는 외부 검색에 더해 **디시·클리앙 사이트 내부 검색**을 브라우저에서 실행하고,
관련 원문과 댓글을 읽었다. 디시 검색 결과의 무관한 게임 정지·고객센터 매크로 답변·광고는 제외했다.

| 글 | 실제로 읽은 내용 | 근거의 한계 |
|---|---|---|
| [디시 카카오톡 갤러리: 카카오톡 영정당함.](https://gall.dcinside.com/mgallery/board/view/?id=kakao&no=61158), 2026-05-14 | 봇 개발자가 봇용 계정이 아닌 본계정의 영구정지를 보고. | 봇 구현·계정별 사용 이력·제재 사유가 없다. AX·DB·LOCO 중 어느 경로인지 분류할 수 없음. |
| [디시 카카오톡 갤러리: 카톡 영정당함](https://gall.dcinside.com/mgallery/board/view/?id=kakao&no=58163), 2025-11-04 | 정상 앱 사용 중 갑자기 로그아웃·정지됐다는 주장. 리밴스드 링크 방문 이력을 언급. | 증빙·구체적인 설치 이력이 없고 댓글에서도 주장의 진위에 이견. 순정 앱의 깨끗한 대조 사례나 AX 피해로 세지 않음. |
| [뽐뿌: 카톡 영구 정지 당함](https://m.ppomppu.co.kr/new/bbs_view.php?id=freeboard&no=9709291), 2025-11-03 | 구버전 사용자의 정지 보고. 후속 댓글에서 금지 앱·프로그램 또는 비정상 네트워크라는 고객센터 답변을 전달. | 앱 취득·설치·과거 변조 여부가 명확하지 않음. 댓글의 리밴스드 추측을 사실로 취급하지 않음. |
| [클리앙: 막장 운영 카카오톡 이용제한 사용기](https://www.clien.net/service/board/use/14606772) | 오픈방에 상품 링크를 올리다 이용제한됐다는 당사자의 보고와 댓글. | 신고·콘텐츠·행동 패턴 가능성이 섞여 있음. 자동화·DB·AX 사례가 아님. |
| [클리앙: 카카오톡 봇이 아직도 남아 있군요](https://www.clien.net/service/board/park/14624999), 2020-02-23 | 과거 봇들이 집단으로 정지됐다는 회고. | 작성자 자신의 피해 기록·구현 방식이 아님. 현재 Mac AX/DB에 직접 적용할 수 없음. |
| [디시 특이점: 카톡 MCP는 왜 못 만들지](https://gall.dcinside.com/mgallery/board/view/?id=thesingularity&no=1507033), 2026-11-01 | Computer Use로 대화를 읽는 속도 문제. | 제재 보고가 아님. |
| [디시 AI 활용: GPT 컴퓨터유즈 관련](https://gall.dcinside.com/mgallery/board/view/?id=ai_utilize&no=104547), 2026-10-01 | 캡처 시간 초과·입력 좌표 오류로 카톡 작업을 완료하지 못했다는 글. | 카카오 계정 차단이 아닌 도구 오류. |

지피터스의 [카카오톡 자동화 실험 글](https://www.gpters.org/chatbot/post/gogseong-1gi-katog-ai-jadonghwa-window-mac-gubun-rut6iIS3e9W6ZeK)은
위험 등급·주의 문구를 제시하지만, Mac AX 단독·DB 단독으로 실제 제재됐다는 당사자 보고는 아니다.
[DB를 연결한 하이브리드 구현 글](https://www.gpters.org/dev/post/kakaotalk-macro-era-how-lEVSOKmyNxOqCtI)은
송신에 LOCO를 유지한다고 명시하므로 DB 전용 또는 DB+AX 사례와 구분했다.

디시에서 `kmsg`, `kakaocli`, `카톡 SQLCipher`, `카톡 MCP`, `카톡 컴퓨터유즈`,
`카톡 봇 영정`, `카톡 매크로 정지`, `카톡 자동화 정지`, `카톡 접근성` 등을 검색했다.
관련 게시물·댓글의 표본을 읽었으며, 전체 결과의 모든 페이지를 전수 조사한 것은 아니다.
클리앙은 `kmsg`, `kakaocli`, `카카오톡 매크로 정지`, `카카오톡 봇 정지`, `카카오톡 DB`로 내부 검색했다.
클리앙 외부 크롤러 접근 제한은 정상 브라우저 조회로 보완했다.
에펨코리아·루리웹도 외부 검색했지만 이 질문에 직접 쓸 수 있는 원문을 확보하지 못했다.
비공개 카페·삭제 글·미색인 게시물까지 확인했다고 주장하지 않는다.

범위를 넓힌 후에도 **AX와 라이브 DB 중 무엇이 더 제재될 가능성이 높은지 순위를 정할
직접적인 비교 사례는 확보하지 못했다.** 다른 경로의 피해담이나 위험 경고만으로 그 공백을 채우지 않는다.
