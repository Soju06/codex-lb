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


## 주요 기능

- 여러 ChatGPT 계정을 한 풀로 묶어서 로드밸런싱
- 계정별 사용량, 토큰, 비용, 최근 추이 확인
- 대시보드에서 API 키 발급 및 키별 제한 설정
- Codex CLI, OpenCode, OpenClaw, OpenAI SDK와 연동
- 업스트림 모델 목록 자동 동기화
- 대시보드 비밀번호 및 선택형 TOTP 인증

## 빠른 시작

컨테이너 실행:

```bash
docker volume create codex-lb-cinamon-data
docker run -d --name codex-lb-cinamon \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-cinamon-data:/var/lib/codex-lb \
  -e CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID=codex-lb-cinamon-local \
  -e CODEX_LB_INSECURE_ALLOW_REMOTE_NO_AUTH=true \
  -e CODEX_LB_INSECURE_ALLOW_REMOTE_NO_AUTH_HOST_CIDRS=172.17.0.0/16 \
  ghcr.io/kgskr/codex-lb:latest
```

또는 로컬 실행:

```bash
uvx codex-lb-cinamon
```

브라우저에서 [http://localhost:2455](http://localhost:2455) 로 접속한 뒤 계정을 추가하면 바로 사용할 수 있습니다.

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
4. 클라이언트에서 `codex-lb-cinamon` 엔드포인트를 사용하도록 설정합니다.

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

```toml
model = "gpt-5.3-codex"
model_reasoning_effort = "xhigh"
model_provider = "codex-lb-cinamon"
# 아래 부분만 붙여 넣으세요.
[model_providers.codex-lb-cinamon]
name = "OpenAI"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
```

API 키 인증을 켠 경우:

```toml
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

추가 메모:

- `CODEX_LB_UPSTREAM_STREAM_TRANSPORT=websocket` 를 주면 업스트림 스트리밍을 WebSocket 우선으로 강제할 수 있습니다.
- 기본값인 `auto`는 Codex 전용 헤더나 모델에 맞춰 적절한 transport를 고릅니다.
- Codex 자체의 실험적 WebSocket 플래그는 계속 `wire_api = "responses"` 와 함께 사용하는 전제입니다.

</details>

<details>
<summary><b>OpenCode</b></summary>

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

</details>

<details>
<summary><b>OpenAI Python SDK</b></summary>

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:2455/v1",
    api_key="sk-clb-...",  # 인증을 끄면 아무 문자열이어도 됩니다.
)

response = client.chat.completions.create(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "안녕하세요"}],
)

print(response.choices[0].message.content)
```

</details>

## API 키 인증(Codex LB 인증용, Platform API key가 아님.)

API 키 인증은 기본적으로 꺼져 있습니다. 켜려면 대시보드의 `Settings -> API Key Auth` 에서 활성화하면 됩니다.

활성화 후에는 모든 클라이언트 요청이 다음 형식을 따라야 합니다.

```text
Authorization: Bearer sk-clb-...
```

API 키는 `Dashboard -> API Keys -> Create` 에서 발급합니다. 전체 키 값은 생성 시 한 번만 표시됩니다.

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
ghcr.io/kgskr/codex-lb
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
