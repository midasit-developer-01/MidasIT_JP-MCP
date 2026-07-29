# auth — 원격 배포용 OAuth 인증

원격(streamable-http) 서버는 호출자마다 자기 MAPI 키가 필요하다. 그런데 **Claude
커스텀 커넥터 UI에는 헤더 입력란이 없어서**(이름·URL·선택적 OAuth 자격증명만 받음)
`X-MIDAS-MAPI-Key` 헤더로 키를 넣을 방법이 없다. 그래서 **서버 자신을 OAuth 2.1
인증 서버로** 만든다: 연결 도중 뜨는 폼에 사용자가 키를 한 번 붙여넣으면, 이후
클라이언트는 폐기 가능한 베어러 토큰만 보낸다. **키는 이 서버 밖으로 안 나간다.**

## 켜는 법 — 환경변수 하나

```
MIDAS_MCP_PUBLIC_URL=https://mcp.example.com   # 외부에서 접속 가능한 https origin
```

이 값이 **있으면 OAuth 켜짐, 없으면 꺼짐.** 스위치이자 주소다 — 서버가 진짜 알아야
하는 건 자기 외부 주소뿐이라(리버스 프록시 뒤라 스스로 못 알아냄), 별도 on/off
플래그를 두지 않는다. 미설정이면 기존과 완전히 동일하게 빌드되어 stdio(`.mcpb`)와
헤더 방식 http 배포는 무영향.

## 흐름

```
1. GET  /.well-known/oauth-protected-resource   \  클라이언트가 먼저 읽는
2. GET  /.well-known/oauth-authorization-server  /  메타데이터 (SDK 자동)
3. POST /register        클라이언트 자동 등록 (DCR) — 커넥터 OAuth 칸을 비워도 되는 이유
4. GET  /authorize  →    우리 /login 폼으로 리다이렉트
5.      /login (폼)       사용자가 MAPI 키 입력 → 인가 코드 발급 → 클라이언트로
6. POST /token           코드 → 액세스/리프레시 토큰 교환 (PKCE 검증은 SDK가 함)
7.      이후 모든 /mcp 요청에 Authorization: Bearer <토큰>
```

토큰 없는 `/mcp` 요청은 `401` + `WWW-Authenticate`로 거부된다 — 이 401이 커넥터에게
"OAuth로 가라"는 신호가 된다. `/authorize`·`/token`·`/register`·`/revoke`·PKCE 검증·
베어러 미들웨어는 **MCP SDK가 전부 제공**한다. 이 패키지가 구현하는 건 저장소와
"로그인이 무엇인가"뿐이다.

## 파일

| 파일 | 역할 |
| --- | --- |
| `store.py` | SQLite 영속화 (clients / pending / codes / tokens) |
| `keys.py` | 신원 = MAPI 키. 형식 검사 + 지문(SHA-256 앞 16자) |
| `pages.py` | 로그인(동의) 화면 HTML — 키 1개만 입력 |
| `provider.py` | OAuth 2.1 인가 서버 본체 (authorize→login→토큰) |
| `__init__.py` | 환경변수 기반 설정, FastMCP 배선, `/login`·`/healthz` 라우트 |
| `check_flow.py` | 종단 검증 스크립트 (런타임 미사용, 배포 게이트) |

## 설계 결정

| 결정 | 근거 |
| --- | --- |
| 키 형식 검사만, 유효성 검사는 안 함 | 만료 키는 첫 API 호출 때 명확한 오류로 드러나는 게 낫다 |
| OAuth `sub` = 키의 SHA-256 앞 16자 | 키를 두 번 저장 않고 사용자 식별. 로그에 키 안 남음 |
| SQLite를 도커 볼륨(`/data`)에 | 이미지 교체에도 인증 유지. 인스턴스 교체 시엔 재인증 |
| 인가 코드는 `authorize()`가 아니라 폼이 발급 | `authorize()` 시점엔 어떤 키를 인가할지 모른다 → `pending`에 보관 후 폼에서 발급 |
| `/login`·`/healthz`는 베어러 미들웨어 면제 | 로그인은 자격증명을 얻는 곳이고, `/healthz`는 인증 없는 생존 확인용 |
| 베어러 폴백 유지 | OAuth 꺼짐이면 베어러를 곧 키로 해석 → 헤더 방식 클라이언트 호환 |
| 리프레시 토큰 로테이션 | 제출된 리프레시는 교환과 함께 폐기 (명세 권고) |

## 로컬 검증

```bash
# 서버 (OAuth 켜짐)
MIDAS_MCP_PUBLIC_URL=http://127.0.0.1:18081 MIDAS_AUTH_DB=./_t/auth.db \
MIDAS_MCP_TRANSPORT=streamable-http MIDAS_MCP_PORT=18081 MIDAS_MCP_HOST=127.0.0.1 \
python -m midas_mcp.server &

# 검증 (401/PKCE/토큰 교환/코드 재사용 불가/리프레시 로테이션 등 19개)
python -m midas_mcp.auth.check_flow http://127.0.0.1:18081
```

## AWS 배포에서 켜기

컨테이너에 `MIDAS_MCP_PUBLIC_URL`을 주고, 토큰 저장소를 볼륨에 둔다. `deploy/RUNBOOK.md`
§인증 참고 — 요약하면 `/opt/midas.env`에 `PUBLIC_URL=https://${ServiceHostname}` 한 줄,
`docker run`에 `-v midas_data:/data -e MIDAS_MCP_PUBLIC_URL="$PUBLIC_URL"`.

> user-data(`redeploy.sh`)는 인스턴스 생애 1회만 기록되므로, 이 변경 반영에는
> **인스턴스 교체(스택 재생성)** 가 필요하다. 이미지 재배포만으로는 안 된다.
