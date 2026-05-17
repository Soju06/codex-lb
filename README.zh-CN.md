<!--
关于
Codex / ChatGPT 账户负载均衡与代理，提供用量追踪、仪表盘和 OpenCode 兼容端点。

主题
python oauth sqlalchemy dashboard load-balancer openai rate-limit api-proxy codex fastapi usage-tracking chatgpt opencode
-->

# codex-lb

[English](./README.md) | **简体中文**

ChatGPT 账户负载均衡器。聚合多个账户、追踪用量、管理 API Key，所有内容在仪表盘中查看。

| ![dashboard](docs/screenshots/dashboard.jpg) | ![accounts](docs/screenshots/accounts.jpg) |
|:---:|:---:|

<details>
<summary>更多截图</summary>

| 设置 | 登录 |
|:---:|:---:|
| ![settings](docs/screenshots/settings.jpg) | ![login](docs/screenshots/login.jpg) |

| 仪表盘（深色） | 账户（深色） | 设置（深色） |
|:---:|:---:|:---:|
| ![dashboard-dark](docs/screenshots/dashboard-dark.jpg) | ![accounts-dark](docs/screenshots/accounts-dark.jpg) | ![settings-dark](docs/screenshots/settings-dark.jpg) |

</details>

## 功能特性

<table>
<tr>
<td><b>账户池化</b><br>在多个 ChatGPT 账户之间负载均衡</td>
<td><b>用量追踪</b><br>按账户记录 token、成本及 28 天趋势</td>
<td><b>API Key</b><br>按 token、成本、时间窗口、模型限流</td>
</tr>
<tr>
<td><b>仪表盘鉴权</b><br>密码 + 可选 TOTP</td>
<td><b>OpenAI 兼容</b><br>支持 Codex CLI、OpenCode 及任意 OpenAI 客户端</td>
<td><b>模型自动同步</b><br>从上游拉取可用模型列表</td>
</tr>
</table>

## 快速开始

```bash
# Docker（推荐）
docker volume create codex-lb-data
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest

# 或者使用 uvx
uvx codex-lb
```

打开 [localhost:2455](http://localhost:2455) → 添加账户 → 完成。

## 远程访问初始化

首次远程访问仪表盘时，需要使用 bootstrap token 来设置初始密码。

**自动生成（默认）**：首次启动且未配置密码时，服务会生成一次性 token 并打印到日志中：

```bash
docker logs codex-lb
# ============================================
#   Dashboard bootstrap token (first-run):
#   <token>
# ============================================
```

打开仪表盘 → 输入 token + 新密码 → 完成。该 token 在所有副本之间共享，并在密码设置完成前持续有效。在多副本部署中，副本之间必须共享同一份加密密钥（Helm chart 默认已配置），重启恢复才能正常工作。

**手动指定 token**：如果想用固定 token，可在启动前设置环境变量：

```bash
docker run -d --name codex-lb \
  -e CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN=your-secret-token \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

**本地访问**（localhost）会完全跳过 bootstrap 流程 —— 不需要 token。

## 客户端配置

将任意 OpenAI 兼容客户端指向 codex-lb 即可。如果启用了 [API Key 鉴权](#api-key-鉴权)，需要将仪表盘中创建的 key 作为 Bearer token 传入。

| Logo | 客户端 | 端点 | 配置 |
|---|--------|----------|--------|
| <img src="https://avatars.githubusercontent.com/u/14957082?s=200" width="32" alt="OpenAI"> | **Codex CLI** | `http://127.0.0.1:2455/backend-api/codex` | `~/.codex/config.toml` |
| <img src="https://avatars.githubusercontent.com/u/208539476?s=200" width="32" alt="OpenCode"> | **OpenCode** | `http://127.0.0.1:2455/v1` | `~/.config/opencode/opencode.json` |
| <img src="https://avatars.githubusercontent.com/u/252820863?s=200" width="32" alt="OpenClaw"> | **OpenClaw** | `http://127.0.0.1:2455/v1` | `~/.openclaw/openclaw.json` |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="32" alt="Python"> | **OpenAI Python SDK** | `http://127.0.0.1:2455/v1` | 代码 |

<details>
<summary><img src="https://avatars.githubusercontent.com/u/14957082?s=200" width="20" align="center" alt="OpenAI">&ensp;<b>Codex CLI / IDE 扩展</b></summary>
<br>

`~/.codex/config.toml`：

```toml
model = "gpt-5.3-codex"
model_reasoning_effort = "xhigh"
model_provider = "codex-lb"

[model_providers.codex-lb]
name = "OpenAI"  # 必填 —— 启用远程 /responses/compact
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true # codex 应用需要
```

可选：在保留 `codex-lb` 池化能力的同时，启用上游原生 WebSocket 流式传输：

```bash
export CODEX_LB_UPSTREAM_STREAM_TRANSPORT=websocket
```

`auto` 是默认值，会对原生 Codex 头部或偏好 WebSocket 的模型自动启用原生 WebSocket。
也可以在仪表盘 设置 → 路由 → 上游流式传输 中切换。

注意：Codex 自身目前并未公开稳定的 `wire_api = "websocket"` provider 模式。
如果想在 Codex 端体验，当前 CLI 暴露了开发中的 feature flag：

```toml
[features]
responses_websockets = true
# 或
responses_websockets_v2 = true
```

这些 flag 仍是实验性的，不能替代 `wire_api = "responses"`。

如果你的部署中上游 WebSocket 握手必须经过环境代理，请设置
`CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV=true`。默认情况下，WebSocket 握手会直连，
以匹配 Codex CLI 原生传输方式。

**配合 [API Key 鉴权](#api-key-鉴权)：**

```toml
[model_providers.codex-lb]
name = "OpenAI"
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
env_key = "CODEX_LB_API_KEY"
supports_websockets = true
requires_openai_auth = true # codex 应用需要
```

```bash
export CODEX_LB_API_KEY="sk-clb-..."   # 从仪表盘获取的 key
codex
```

**验证 WebSocket 传输**

通过一次性的 debug 运行进行验证：

```bash
RUST_LOG=debug codex exec "Reply with OK only."
```

WebSocket 健康信号：

- CLI 日志中包含 `connecting to websocket` 与 `successfully connected to websocket`
- `codex-lb` 日志中出现 `WebSocket /backend-api/codex/responses`
- `codex-lb` 日志中**不应**出现同次运行的回退 `POST /backend-api/codex/responses`

如果在反向代理后运行 `codex-lb`，请确保反代会转发 WebSocket upgrade。

**从直连 OpenAI 迁移** —— `codex resume` 会按 `model_provider` 过滤；
旧会话不会出现，除非重新打标：

```bash
# JSONL 会话文件（所有版本）
find ~/.codex/sessions -name '*.jsonl' \
  -exec sed -i '' 's/"model_provider":"openai"/"model_provider":"codex-lb"/g' {} +

# SQLite 状态库（>= v0.105.0，会创建 ~/.codex/state_*.sqlite）
sqlite3 ~/.codex/state_5.sqlite \
  "UPDATE threads SET model_provider = 'codex-lb' WHERE model_provider = 'openai';"
```

</details>

<details>
<summary><img src="https://avatars.githubusercontent.com/u/208539476?s=200" width="20" align="center" alt="OpenCode">&ensp;<b>OpenCode</b></summary>
<br>

> **重要**：请使用内置 `openai` provider 并覆盖 `baseURL`，**不要**用 `@ai-sdk/openai-compatible` 自定义 provider。自定义 provider 走的是 Chat Completions API，会**丢失推理 / thinking 内容**。内置 `openai` provider 走 Responses API，会正确保留 `encrypted_content` 与多轮推理状态。

开始之前，请确保清理 `~/.local/share/opencode/auth.json` 中已有的 OpenAI 凭据。
可以用以下一行命令清理：
`jq 'del(.openai)' ~/.local/share/opencode/auth.json > auth.json.tmp && mv auth.json.tmp ~/.local/share/opencode/auth.json`

`~/.config/opencode/opencode.json`：

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
        },
        "gpt-5.1-codex-mini": {
          "name": "GPT-5.1 Codex Mini",
          "reasoning": true,
          "options": { "reasoningEffort": "high", "reasoningSummary": "detailed" },
          "limit": { "context": 272000, "output": 65536 }
        },
        "gpt-5.3-codex-spark": {
          "name": "GPT-5.3 Codex Spark",
          "reasoning": true,
          "options": { "reasoningEffort": "xhigh", "reasoningSummary": "detailed" },
          "limit": { "context": 128000, "output": 65536 }
        }
      }
    }
  },
  "model": "openai/gpt-5.3-codex"
}
```

这样会将内置 `openai` provider 的端点指向 codex-lb，同时保留正确处理推理的 Responses API 代码路径。

```bash
export CODEX_LB_API_KEY="sk-clb-..."   # 从仪表盘获取的 key
opencode
```

</details>

<details>
<summary><img src="https://avatars.githubusercontent.com/u/252820863?s=200" width="20" align="center" alt="OpenClaw">&ensp;<b>OpenClaw</b></summary>
<br>

`~/.openclaw/openclaw.json`：

```jsonc
{
  "agents": {
    "defaults": {
      "model": { "primary": "codex-lb/gpt-5.4" },
      "models": {
        "codex-lb/gpt-5.4": { "params": { "cacheRetention": "short" } }
        "codex-lb/gpt-5.4-mini": { "params": { "cacheRetention": "short" } }
        "codex-lb/gpt-5.3-codex": { "params": { "cacheRetention": "short" } }
      }
    }
  },
  "models": {
    "mode": "merge",
    "providers": {
      "codex-lb": {
        "baseUrl": "http://127.0.0.1:2455/v1",
        "apiKey": "${CODEX_LB_API_KEY}",   // 如果 API Key 鉴权未启用，可填 "dummy"
        "api": "openai-responses",
        "models": [
          {
            "id": "gpt-5.4",
            "name": "gpt-5.4 (codex-lb)",
            "contextWindow": 1050000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          },
          {
            "id": "gpt-5.4-mini",
            "name": "gpt-5.4-mini (codex-lb)",
            "contextWindow": 400000,
            "contextTokens": 272000,
            "maxTokens": 4096,
            "input": ["text"],
            "reasoning": false
          },
          {
            "id": "gpt-5.3-codex",
            "name": "gpt-5.3-codex (codex-lb)",
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

设置环境变量，或将 `${CODEX_LB_API_KEY}` 替换为仪表盘中的 key。如果 API Key 鉴权已禁用，
本地请求可省略 key，但非本地请求仍会被拒绝，直到完成代理鉴权配置。

</details>

<details>
<summary><img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="20" align="center" alt="Python">&ensp;<b>OpenAI Python SDK</b></summary>
<br>

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:2455/v1",
    api_key="sk-clb-...",  # 仪表盘获取，鉴权关闭时填任意非空字符串即可
)

response = client.chat.completions.create(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

</details>

## API Key 鉴权

API Key 鉴权**默认关闭**。在该模式下，对受保护代理路由的请求只有本地访问能直接放行；非本地请求在未配置代理鉴权之前会被拒绝。当客户端通过远程网络、Docker、虚拟机或容器网络（被服务视为非本地）连接时，请在仪表盘的 **设置 → API Key 鉴权** 中启用。

启用后，客户端必须将有效的 API Key 作为 Bearer token 传入：

```
Authorization: Bearer sk-clb-...
```

受该设置覆盖的代理路由有：

- `/v1/*`（除 `/v1/usage` 外，该路由始终需要有效 key）
- `/backend-api/codex/*`
- `/backend-api/transcribe`

**创建 key**：仪表盘 → API Keys → 创建。完整 key 仅在创建时显示**一次**。Key 支持可选过期时间、模型限制以及限流（按 token / 成本、按天 / 周 / 月）。

## 配置

通过 `CODEX_LB_` 前缀的环境变量或 `.env.local` 配置，详见 [`.env.example`](.env.example)。
默认数据库后端是 SQLite；可选通过 `CODEX_LB_DATABASE_URL` 切换到 PostgreSQL（例如 `postgresql+asyncpg://...`）。

### 仪表盘鉴权模式

`codex-lb` 通过环境变量支持三种仪表盘鉴权模式：

- `CODEX_LB_DASHBOARD_AUTH_MODE=standard` —— 内置仪表盘密码，可在设置页可选启用 TOTP。
- `CODEX_LB_DASHBOARD_AUTH_MODE=trusted_header` —— 信任反向代理传入的鉴权头（如 Authelia 的 `Remote-User`），但仅对来自 `CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS` 的请求生效。内置密码 / TOTP 仍可作为可选回退；管理密码 / TOTP 仍需要回退密码会话。
- `CODEX_LB_DASHBOARD_AUTH_MODE=disabled` —— 完全跳过仪表盘鉴权。仅在网络受限或外部已有鉴权时使用。该模式下内置密码 / TOTP 管理被禁用。

`trusted_header` 模式还需要：

```bash
CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS=true
CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS=172.18.0.0/16
CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER=Remote-User
```

如果信任头缺失且未配置回退密码，仪表盘会 fail-closed 并显示"需要反向代理"的提示，而不是加载 UI。

### Docker 示例

**Authelia / 信任头**

```bash
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -e CODEX_LB_DASHBOARD_AUTH_MODE=trusted_header \
  -e CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER=Remote-User \
  -e CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS=true \
  -e CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS=172.18.0.0/16 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

**强制覆盖 / 无应用层仪表盘鉴权**

```bash
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -e CODEX_LB_DASHBOARD_AUTH_MODE=disabled \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

Helm 部署可通过 `extraEnv` 传入相同变量。

## 数据

| 环境 | 路径 |
|-------------|------|
| 本地 / uvx | `~/.codex-lb/` |
| Docker | `/var/lib/codex-lb/` |

请备份此目录以保留你的数据。

## Kubernetes

```bash
helm install codex-lb oci://ghcr.io/soju06/charts/codex-lb \
  --set postgresql.auth.password=changeme \
  --set config.databaseMigrateOnStartup=true \
  --set migration.schemaGate.enabled=false
kubectl port-forward svc/codex-lb 2455:2455
```

打开 [localhost:2455](http://localhost:2455) → 添加账户 → 完成。

Helm chart 会基于 headless service 的 per-pod DNS 名称为多副本部署自动配置 HTTP `/responses` owner handoff。默认集群域名为 `cluster.local`；如果你的集群使用其它后缀，请在 Helm 中设置 `clusterDomain`。仅当 pod 必须通过其它内部地址访问时才需要覆盖 `config.sessionBridgeAdvertiseBaseUrl`。

外部数据库、生产环境配置、Ingress、可观测性等更多内容，请参考 [Helm chart README](deploy/helm/codex-lb/README.md)。

## 开发

```bash
# Docker
docker compose watch

# 本地
uv sync && cd frontend && bun install && cd ..
uv run fastapi run app/main.py --reload        # 后端 :2455
cd frontend && bun run dev                     # 前端 :5173
```

## 贡献者 ✨

完整的贡献者名单请参见英文 [README](./README.md#contributors-) 中由 [all-contributors](https://github.com/all-contributors/all-contributors) 自动生成的列表。该项目欢迎任何形式的贡献！
