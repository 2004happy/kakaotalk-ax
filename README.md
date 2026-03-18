# kakaotalk-reader

카카오톡 Mac 버전의 로컬 암호화 DB를 복호화하여 채팅 로그를 읽는 CLI 도구 + MCP 서버.

## 요구사항

- macOS (카카오톡 Mac 설치 필요)
- Python 3.10+
- sqlcipher

```bash
brew install sqlcipher
```

## 설치

```bash
pip install -e .
```

## CLI 사용법

```bash
# 설정 확인
kakaotalk-reader info

# 초기 세팅 (userId 자동 탐지 + 검증)
kakaotalk-reader setup

# 채팅방 목록
kakaotalk-reader rooms

# 특정 채팅방 메시지 읽기
kakaotalk-reader read <room_id> -n 100

# 최근 메시지
kakaotalk-reader recent -n 30
kakaotalk-reader recent --since today
kakaotalk-reader recent --since yesterday --until yesterday
kakaotalk-reader recent --since 2026-03-01 --until 2026-03-07
kakaotalk-reader recent --since="-3d" -n 100

# 메시지 검색
kakaotalk-reader search "키워드" -n 50

# 유저 목록
kakaotalk-reader users

# JSON 출력
kakaotalk-reader --format json recent --since today

# 직접 SQL
kakaotalk-reader sql "SELECT * FROM NTChatRoom LIMIT 10"
```

## MCP 서버 (Claude Desktop / Cowork)

`claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "kakaotalk": {
      "command": "/opt/homebrew/bin/python3",
      "args": ["/path/to/src/kakaotalk_mcp.py"]
    }
  }
}
```

Claude Code에서 추가:

```bash
claude mcp add --transport stdio kakaotalk -- python3 /path/to/src/kakaotalk_mcp.py
```

## DB 테이블 구조

| 테이블 | 주요 컬럼 |
|--------|-----------|
| `NTChatRoom` | chatId, type, chatName, linkId, lastLogId, directChatMemberUserId |
| `NTChatMessage` | chatId, logId, authorId, type, sentAt, message, attachment |
| `NTUser` | userId, linkId, nickName |

## 동작 원리

1. macOS 하드웨어 UUID 자동 획득
2. plist에서 카카오톡 userId 추출 (실패 시 브루트포스)
3. UUID + userId로 AES 복호화 키 생성 (PBKDF2-SHA256)
4. sqlcipher로 암호화된 SQLite DB 쿼리

## 참고

- [Mac 복호화 코드 (blluv)](https://gist.github.com/blluv/8418e3ef4f4aa86004657ea524f2de14)
- [Mac 복호화 논문](https://koreascience.kr/article/JAKO202331137843699.page)
