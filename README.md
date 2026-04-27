<!--
About
ChatGPT/Codex account load balancer and proxy with usage tracking, dashboard, and OpenAI-compatible endpoints

Topics
python oauth sqlalchemy dashboard load-balancer openai rate-limit api-proxy codex fastapi usage-tracking chatgpt opencode

Resources
-->

# codex-lb-cinamon

여러 ChatGPT 계정을 묶어서 사용량을 관리하고, Codex CLI나 OpenAI 호환 클라이언트에서 공통 엔드포인트로 붙을 수 있게 해주는 프록시입니다. 대시보드에서 계정, API 키, 사용량, 최근 요청을 한 곳에서 관리할 수 있습니다.

이 포크 `codex-lb-cinamon`은 [codex-lb](https://github.com/Soju06/codex-lb)를 기반으로, `OpenAI Platform API key`를 보조 upstream으로 등록해 ChatGPT 계정들의 사용량이 모두 소진되었을 때 fallback으로 사용할 수 있도록 수정한 버전입니다. 즉 기본 경로는 계속 ChatGPT 계정 풀을 사용하고, 필요할 때만 Platform API로 우회하는 개인/사내 운영용 포크를 목표로 합니다.

## Platform fallback 주의사항

- `openai_platform`은 여전히 fallback 전용입니다. 최소 1개의 활성 `chatgpt_web` 계정이 있어야 등록과 라우팅이 가능합니다.
- 일반 fallback은 모든 호환 ChatGPT 계정이 drain 기준을 넘었을 때만 활성화됩니다.
  - 기본 기준: `primary remaining <= 10%`, `secondary remaining <= 5%`
- `CODEX_LB_PLATFORM_FALLBACK_FORCE_ENABLED=true`를 주면 usage drain 여부와 무관하게 fallback 판정을 강제할 수 있습니다.
- `backend_codex_http`를 켜면 HTTP `GET /backend-api/codex/models` 와 HTTP `POST /backend-api/codex/responses` 가 Platform fallback 후보가 됩니다.
- HTTP `POST /backend-api/codex/responses` 에서 `session_id`, `x-codex-session-id`, `x-codex-conversation-id`, `x-codex-turn-state` 같은 Codex 세션 헤더는 fallback을 막지 않습니다.
- 하지만 payload의 `conversation` 또는 `previous_response_id` 는 여전히 Platform에서 지원하지 않으므로 fallback 대상이 아닙니다.
- websocket `/backend-api/codex/responses`, `/v1/chat/completions`, compact 경로는 phase 1에서 Platform fallback을 지원하지 않습니다.


## 주요 기능

- 여러 ChatGPT 계정을 한 풀로 묶어서 로드밸런싱
- 계정별 사용량, 토큰, 비용, 최근 추이 확인
- 대시보드에서 API 키 발급 및 키별 제한 설정
- Codex CLI, OpenCode, OpenClaw, OpenAI SDK와 연동
- 업스트림 모델 목록 자동 동기화
- 대시보드 비밀번호 및 선택형 TOTP 인증

## 빠른 시작

PyPI로 설치:

macOS / Linux:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install codex-lb-cinamon
codex-lb-cinamon start
# 동작 상태 확인
codex-lb-cinamon status
# 종료 시
codex-lb-cinamon shutdown
```

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
pip install codex-lb-cinamon
codex-lb-cinamon start
# 동작 상태 확인
codex-lb-cinamon status
# 종료 시
codex-lb-cinamon shutdown
```

포그라운드로 명시 실행:

```bash
codex-lb-cinamon serve
```

기본 PID 파일과 로그 파일은 아래 경로를 사용합니다.

```text
macOS / Linux: ~/.codex-lb/server.pid
macOS / Linux: ~/.codex-lb/server.log
Windows: %USERPROFILE%\.codex-lb\server.pid
Windows: %USERPROFILE%\.codex-lb\server.log
```

원하면 `start`에는 `--pid-file`, `--log-file`, `--host`, `--port`를 함께 줄 수 있고, `serve`에는 `--host`, `--port`, `--ssl-certfile`, `--ssl-keyfile`를 함께 줄 수 있습니다.

## Remote Setup

원격에서 처음 대시보드 비밀번호를 설정할 때는 bootstrap token이 필요합니다.

자동 생성(기본):

```bash
docker logs codex-lb-cinamon
# ============================================
#   Dashboard bootstrap token (first-run):
#   <token>
# ============================================
```

비밀번호가 아직 없고 `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` 을 지정하지 않았다면 서버가 1회용 bootstrap token을 생성해 로그에 남깁니다. 여러 replica가 같은 암호화 키를 사용하면 재시작 뒤에도 같은 토큰을 복구해 다시 로그로 확인할 수 있습니다.

수동 지정:

```bash
docker run -d --name codex-lb-cinamon \
  -e CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN=your-secret-token \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-cinamon-data:/var/lib/codex-lb \
  ghcr.io/cinev/codex-lb-cinamon:latest
```

`localhost` 나 host-OS bypass 로 분류되는 요청은 bootstrap token 없이도 초기 설정을 진행할 수 있습니다.

DB 마이그레이션을 수동으로 실행해야 하면:

```bash
codex-lb-cinamon-db upgrade head
```

브라우저에서 [http://localhost:2455](http://localhost:2455) 로 접속한 뒤 계정을 추가하면 바로 사용할 수 있습니다.

컨테이너 실행:

```bash
docker volume create codex-lb-cinamon-data
docker run -d --name codex-lb-cinamon \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-cinamon-data:/var/lib/codex-lb \
  -e CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID=codex-lb-cinamon-local \
  -e CODEX_LB_INSECURE_ALLOW_REMOTE_NO_AUTH=true \
  -e CODEX_LB_INSECURE_ALLOW_REMOTE_NO_AUTH_HOST_CIDRS=172.17.0.0/16 \
  ghcr.io/cinev/codex-lb-cinamon:latest
```

또는 로컬 실행:

```bash
uvx codex-lb-cinamon
```

명령은 `serve`와 동일하게 foreground 서버를 바로 띄우며, 필요하면 추가 인자를 그대로 넘길 수 있습니다.

컨테이너로 실행할 때는 아래 설정을 함께 주는 것을 권장합니다.

- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID=codex-lb-cinamon-local`
  - 컨테이너 재시작 시 bridge instance id가 흔들리지 않게 해서 세션 브리지 안정성을 높입니다.
- `CODEX_LB_INSECURE_ALLOW_REMOTE_NO_AUTH=true`
  - 로컬 네트워크나 사내 개인용처럼 제한된 환경에서 로그인, bootstrap, proxy API key 인증 없이 바로 붙을 수 있게 합니다.
  - 테스트/내부 사용 전용이며, 외부에 노출되는 환경에는 권장하지 않습니다.

Docker를 쓴다면 아래 CIDR 설정도 함께 주는 편이 안전합니다.

- `CODEX_LB_INSECURE_ALLOW_REMOTE_NO_AUTH_HOST_CIDRS=172.17.0.0/16`
  - Docker bridge 환경에서 호스트 OS에서 들어오는 요청만 무인증으로 허용하려면 함께 주는 것을 권장합니다.
  - Docker 기본 bridge 대역 예시입니다. 네트워크 설정이 다르면 실제 bridge CIDR에 맞게 바꿔야 합니다.

[Podman](https://podman.io/docs/installation)을 쓴다면 위 CIDR 값은 그대로 쓰지 말고, 환경에 맞는 bridge 대역을 넣거나 자동 감지에 맡기세요. 예를 들어 rootless Podman은 `10.88.0.0/16` 계열인 경우가 많습니다.

> Podman은 rootless로 쓰기 쉽고 비교적 가벼운 컨테이너 런타임이라, Docker가 무겁다고 느껴지면 한 번 써볼 만합니다.


## 첫 설정

1. 대시보드에 접속합니다.
2. ChatGPT 계정과 Platform API Key를 추가합니다.
3. 클라이언트에서 `codex-lb-cinamon` 엔드포인트를 사용하도록 설정합니다.

### Platform API Key 등록

대시보드에서 Platform 키를 넣을 때는 `Accounts` 페이지에서 `Add OpenAI Platform API key`를 사용하면 됩니다.

입력 항목:

- `Label`: 대시보드에서 구분할 이름
- `API key`: OpenAI Platform API 키
- `Organization`, `Project`: 쓰는 경우만 입력, 비워둬도 됨

중요:

- Platform API key는 단독으로 쓸 수 없습니다. 먼저 활성 ChatGPT 계정이 최소 1개 있어야 등록됩니다.
- `Eligible routes`를 하나도 체크하지 않으면 등록은 되어도 실제 라우팅에는 쓰이지 않습니다.

Codex app이나 Codex CLI에 붙일 거면 `Eligible routes`의 체크박스 3개를 **전부 체크**하세요.

- `Fallback HTTP /v1/models`
- `Fallback stateless HTTP /v1/responses`
- `Fallback HTTP /backend-api/codex`

가장 단순한 기준으로 보면:

- Codex app / Codex CLI 사용: 위 3개 전부 체크
- 일반 OpenAI 호환 `/v1` 클라이언트만 사용: 보통 `/v1/models`, `/v1/responses` 쪽만 체크하면 됨

## 클라이언트 연결

OpenAI 호환 클라이언트는 모두 `codex-lb-cinamon`을 upstream처럼 사용할 수 있습니다. API 키 인증을 켠 경우 대시보드에서 발급한 키를 Bearer 토큰으로 넣어야 합니다.

| 클라이언트 | 엔드포인트 | 설정 위치 |
|---|---|---|
| Codex CLI | `http://127.0.0.1:2455/backend-api/codex` | `~/.codex/config.toml` |
| OpenCode | `http://127.0.0.1:2455/v1` | `~/.config/opencode/opencode.json` |
| OpenClaw | `http://127.0.0.1:2455/v1` | `~/.openclaw/openclaw.json` |
| OpenAI Python SDK | `http://127.0.0.1:2455/v1` | 코드에서 설정 |

<details>
<summary><b>Codex CLI / IDE 확장</b></summary>

`~/.codex/config.toml`:

가장 쉽게 말하면:

- `model_provider = "codex-lb-cinamon"` 는 파일 맨 위에 넣습니다.
- `[model_providers.codex-lb-cinamon]` 블록은 파일 맨 아래에 넣습니다.

1. 먼저 아래 1줄을 파일 **맨 위**에 넣습니다.
   - `[ ... ]` 로 시작하는 다른 섹션 안에 넣으면 안 됩니다.
   - `model` 이나 `model_reasoning_effort` 는 여기서 건드릴 필요 없습니다.
   - 이미 `model_provider`가 있다면 그 값만 `codex-lb-cinamon`으로 바꾸면 됩니다.

```toml
model_provider = "codex-lb-cinamon"
```

2. 그다음 아래 **공급자 정의 블록**을 파일 **맨 아래**에 그대로 붙여 넣습니다.
   - 이 블록은 다른 `[ ... ]` 섹션들 아래에 따로 들어가면 됩니다.

```toml
[model_providers.codex-lb-cinamon]
name = "OpenAI"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
```

즉, 최종 파일 모양은 아래처럼 됩니다.

```toml
model_provider = "codex-lb-cinamon"

[model_providers.codex-lb-cinamon]
name = "OpenAI"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
```

codex-lb-cinamon의 API 키 인증을 켠 경우에는 아래처럼 추가합니다.

```toml
model_provider = "codex-lb-cinamon"

[model_providers.codex-lb-cinamon]
name = "OpenAI"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
env_key = "CODEX_LB_API_KEY"
supports_websockets = true
requires_openai_auth = true
```

```bash
export CODEX_LB_API_KEY="sk-clb-..."
codex
```

정리하면:

- `model_provider = "codex-lb-cinamon"`: 최상위 설정
- `[model_providers.codex-lb-cinamon]`: 공급자 상세 정의 블록
- 두 이름 `codex-lb-cinamon`은 반드시 서로 같아야 합니다.
- `model` / `model_reasoning_effort` 는 기존 Codex 설정을 그대로 써도 됩니다.

추가 메모:

- `CODEX_LB_UPSTREAM_STREAM_TRANSPORT=websocket` 를 주면 업스트림 스트리밍을 WebSocket 우선으로 강제할 수 있습니다.
- 기본값인 `auto`는 Codex 전용 헤더나 모델에 맞춰 적절한 transport를 고릅니다.
- Codex 자체의 실험적 WebSocket 플래그는 계속 `wire_api = "responses"` 와 함께 사용하는 전제입니다.

</details>

<details>
<summary><b>OpenCode</b></summary>

Before starting, please ensure that all existing OpenAI credentials is cleared in `~/.local/share/opencode/auth.json`
You can clean the config by using this one-liner
`jq 'del(.openai)' ~/.local/share/opencode/auth.json > auth.json.tmp && mv auth.json.tmp ~/.local/share/opencode/auth.json`

`~/.config/opencode/opencode.json`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "openai": {
      "options": {
        "baseURL": "http://127.0.0.1:2455/v1",
        "apiKey": "{env:CODEX_LB_API_KEY}"
      },
      "models": {
        "gpt-5.4": {
          "name": "GPT-5.4",
          "reasoning": true,
          "options": { "reasoningEffort": "high", "reasoningSummary": "detailed" },
          "limit": { "context": 1050000, "output": 128000 }
        },
        "gpt-5.3-codex": {
          "name": "GPT-5.3 Codex",
          "reasoning": true,
          "options": { "reasoningEffort": "high", "reasoningSummary": "detailed" },
          "limit": { "context": 272000, "output": 65536 }
        }
      }
    }
  },
  "model": "openai/gpt-5.3-codex"
}
```

```bash
export CODEX_LB_API_KEY="sk-clb-..."
opencode
```

</details>

<details>
<summary><b>OpenClaw</b></summary>

`~/.openclaw/openclaw.json`:

```jsonc
{
  "agents": {
    "defaults": {
      "model": { "primary": "codex-lb-cinamon/gpt-5.4" },
      "models": {
        "codex-lb-cinamon/gpt-5.4": { "params": { "cacheRetention": "short" } },
        "codex-lb-cinamon/gpt-5.4-mini": { "params": { "cacheRetention": "short" } },
        "codex-lb-cinamon/gpt-5.3-codex": { "params": { "cacheRetention": "short" } }
      }
    }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "codex-lb-cinamon": {
        "baseUrl": "http://127.0.0.1:2455/v1",
        "apiKey": "${CODEX_LB_API_KEY}",
        "api": "openai-responses",
        "models": [
          {
            "id": "gpt-5.4",
            "name": "gpt-5.4 (codex-lb-cinamon)",
            "contextWindow": 1050000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          },
          {
            "id": "gpt-5.4-mini",
            "name": "gpt-5.4-mini (codex-lb-cinamon)",
            "contextWindow": 400000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          }
        ]
      }
    }
  }
}
```

`CODEX_LB_API_KEY` 환경 변수를 쓰거나 `${CODEX_LB_API_KEY}` 자리에 대시보드에서 발급한 키를 넣으면 됩니다. API 키 인증이 꺼져 있어도 비로컬 요청은 proxy 인증이 준비되기 전까지 거절될 수 있습니다.
</details>

<details>
<summary><b>OpenAI Python SDK</b></summary>

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:2455/v1",
    api_key="sk-clb-...",  # 대시보드에서 발급한 키. 인증이 꺼져 있으면 아무 non-empty 문자열도 가능
)

response = client.chat.completions.create(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "안녕하세요"}],
)

print(response.choices[0].message.content)
```

</details>

## API 키 인증(Codex LB 인증용, Platform API key가 아님.)

API 키 인증은 기본적으로 꺼져 있습니다. 이 상태에서는 보호된 프록시 라우트에 대해 로컬 요청만 키 없이 통과하고, 비로컬 요청은 proxy 인증이 준비될 때까지 거절됩니다. Docker, VM, 원격 네트워크처럼 서비스가 비로컬로 인식하는 환경에서 붙는 클라이언트는 보통 대시보드의 `Settings -> API Key Auth` 에서 이 기능을 켜고 키를 사용해야 합니다.

활성화 후에는 모든 클라이언트 요청이 다음 형식을 따라야 합니다.

```text
Authorization: Bearer sk-clb-...
```

적용 라우트:

- `/v1/*`
- `/backend-api/codex/*`
- `/backend-api/transcribe`

`/api/codex/usage` 는 별도 caller-identity 경로라 대시보드 세션만으로는 접근할 수 없습니다.

API 키는 `Dashboard -> API Keys -> Create` 에서 발급하며, 전체 키 값은 생성 시 한 번만 표시됩니다.

지원 항목:

- 만료일
- 허용 모델 제한
- 강제 모델
- 토큰 / 비용 / 기간 기반 제한

## 설정

- 환경 변수는 `CODEX_LB_` 접두어를 사용합니다.
- 예시는 `.env.example` 에 있습니다.
- 대시보드 인증 설정은 UI에서 변경할 수 있습니다.
- 기본 DB는 SQLite이며, `CODEX_LB_DATABASE_URL` 을 주면 PostgreSQL도 사용할 수 있습니다.

### 대시보드 인증 모드

`codex-lb-cinamon` 은 다음 3가지 대시보드 인증 모드를 지원합니다.

- `CODEX_LB_DASHBOARD_AUTH_MODE=standard`
  - 기본 내장 비밀번호 인증과 선택형 TOTP를 사용합니다.
- `CODEX_LB_DASHBOARD_AUTH_MODE=trusted_header`
  - 리버스 프록시가 주입한 인증 헤더를 신뢰합니다.
  - `CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS=true`, `CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS`, `CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER` 설정이 함께 필요합니다.
  - 내장 비밀번호/TOTP는 fallback 으로 계속 둘 수 있습니다.
- `CODEX_LB_DASHBOARD_AUTH_MODE=disabled`
  - 앱 레벨 대시보드 인증을 완전히 우회합니다.
  - 외부 인증이나 네트워크 제한이 있는 환경에서만 쓰는 것이 안전합니다.

`trusted_header` 예시:

```bash
CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS=true
CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS=172.18.0.0/16
CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER=Remote-User
```

신뢰 헤더가 없고 fallback 비밀번호도 설정되지 않았다면 대시보드는 fail-closed 로 동작합니다.

## 데이터 위치

| 실행 방식 | 경로 |
|---|---|
| 로컬 / `uvx codex-lb-cinamon` | `~/.codex-lb/` |
| 컨테이너 | `/var/lib/codex-lb/` |

이 디렉터리를 백업하면 계정, 설정, 키, 로그를 보존할 수 있습니다.

## 배포 메모

이 포크는 컨테이너 배포를 기준으로 문서를 유지합니다. Kubernetes/Helm 관련 절차는 이 README에서 다루지 않습니다.

이미지 주소:

```text
ghcr.io/cinev/codex-lb-cinamon
```

## 개발

```bash
# 백엔드 + 프론트 개발 서버
uv sync
cd frontend && bun install && cd ..
uv run fastapi run app/main.py --reload
cd frontend && bun run dev
```

컨테이너 기반 개발:

```bash
docker compose watch
```

기본 포트:

- 백엔드: `2455`
- 프론트 개발 서버: `5173`
