<!--
关于
Codex / ChatGPT 账户负载均衡与代理，提供用量追踪、仪表盘和 OpenCode 兼容端点。

主题
python oauth sqlalchemy dashboard load-balancer openai rate-limit api-proxy codex fastapi usage-tracking chatgpt opencode
-->

# codex-lb

![codex-lb](docs/screenshots/banner.jpg)

[English](./README.md) | **简体中文**

> **文档站点（英文，权威版本）**: <https://soju06.github.io/codex-lb/> — 本页内容可能滞后，最新使用说明以英文文档站点为准。

ChatGPT 账户负载均衡器。聚合多个账户、追踪用量、管理 API Key，所有内容在仪表盘中查看。

**文档: <https://soju06.github.io/codex-lb/>** — 快速上手、客户端配置、配置项、部署、故障排查以及更多截图。

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

| ![dashboard](docs/screenshots/dashboard.jpg) | ![accounts](docs/screenshots/accounts.jpg) |
|:---:|:---:|

## 快速开始

```bash
# Docker（推荐）
docker volume create codex-lb-data
docker network inspect codex-lb-net >/dev/null 2>&1 || docker network create codex-lb-net
docker run -d --name codex-lb \
  --network codex-lb-net \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest

# 或者使用 uvx
uvx codex-lb

# 或者使用 nix
nix run github:Soju06/codex-lb
```

打开 [localhost:2455](http://localhost:2455) → 添加账户 → 完成。

首次远程访问仪表盘？需要一次性的 bootstrap token ——
参见 [快速上手](https://soju06.github.io/codex-lb/getting-started/)。

## 客户端配置

将任意 OpenAI 兼容客户端指向 codex-lb 即可。以 Codex CLI 为例，`~/.codex/config.toml`：

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "xhigh"
model_provider = "codex-lb"

[model_providers.codex-lb]
name = "openai"  # 必填 —— 启用远程 /responses/compact。自 Codex 2026-05-23 起须为小写；旧写法 "OpenAI" 无法解析 gpt-5.5
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true # codex 应用需要
```

| Logo | 客户端 | 端点 | 指南 |
|---|--------|----------|-------|
| <img src="https://avatars.githubusercontent.com/u/14957082?s=200" width="32" alt="OpenAI"> | **Codex CLI / IDE** | `http://127.0.0.1:2455/backend-api/codex` | [客户端配置 → Codex CLI](https://soju06.github.io/codex-lb/client-setup/#codex-cli-ide-extension) |
| <img src="https://avatars.githubusercontent.com/u/66570915?s=200" width="32" alt="OpenCode (Anomaly)"> | **OpenCode** | `http://127.0.0.1:2455/v1` | [客户端配置 → OpenCode](https://soju06.github.io/codex-lb/client-setup/#opencode) |
| <img src="https://avatars.githubusercontent.com/u/252820863?s=200" width="32" alt="OpenClaw"> | **OpenClaw** | `http://127.0.0.1:2455/v1` | [客户端配置 → OpenClaw](https://soju06.github.io/codex-lb/client-setup/#openclaw) |
| <img src="https://avatars.githubusercontent.com/u/134168893?s=200" width="32" alt="Hermes Agent (Nous Research)"> | **Hermes Agent** | `http://127.0.0.1:2455/v1` | [客户端配置 → Hermes Agent](https://soju06.github.io/codex-lb/client-setup/#hermes-agent) |
| <img src="https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg" width="32" alt="Python"> | **OpenAI Python SDK** | `http://127.0.0.1:2455/v1` | [客户端配置 → Python SDK](https://soju06.github.io/codex-lb/client-setup/#openai-python-sdk) |

远程客户端需要在仪表盘中创建的 [API Key](https://soju06.github.io/codex-lb/api-keys/)。

## 配置

通过 `CODEX_LB_` 前缀的环境变量或 `.env.local` 配置 —— 详见 [`.env.example`](.env.example) 与
[配置指南](https://soju06.github.io/codex-lb/configuration/)。默认数据库后端是 SQLite；
可选通过 `CODEX_LB_DATABASE_URL` 切换到 PostgreSQL。

## 数据

| 环境 | 路径 |
|-------------|------|
| 本地 / uvx | `~/.codex-lb/` |
| Docker | `/var/lib/codex-lb/` |

请备份此目录以保留你的数据。

## 文档

完整文档位于 **<https://soju06.github.io/codex-lb/>**：

- [快速上手](https://soju06.github.io/codex-lb/getting-started/) —— 快速开始、远程访问 bootstrap token
- [客户端配置](https://soju06.github.io/codex-lb/client-setup/) —— Codex CLI、OpenCode、OpenClaw、Python SDK
- [配置](https://soju06.github.io/codex-lb/configuration/) —— 真正重要的少数设置项
- [鉴权](https://soju06.github.io/codex-lb/authentication/) —— 仪表盘鉴权模式
- [API Key](https://soju06.github.io/codex-lb/api-keys/) —— 保护代理路由
- [路由](https://soju06.github.io/codex-lb/routing/) —— 策略指南
- [数据库](https://soju06.github.io/codex-lb/database/) —— SQLite / PostgreSQL、Postgres 16 → 18 升级
- [部署](https://soju06.github.io/codex-lb/deployment/docker/) —— [Docker](https://soju06.github.io/codex-lb/deployment/docker/)、[Kubernetes](https://soju06.github.io/codex-lb/deployment/kubernetes/)、[远程访问](https://soju06.github.io/codex-lb/deployment/remote/)
- [故障排查](https://soju06.github.io/codex-lb/troubleshooting/)

### 社区伴生项目

由社区独立维护、消费仪表盘 API 的项目，不属于 codex-lb 本体
（访问指引见 [文档中的列表](https://soju06.github.io/codex-lb/#community-companions)）：

- [Codex LB Status Bar](https://github.com/sm1ee/codex-lb-statusbar) —— 原生 macOS 应用：账户状态、配额详情、账户控制
- [codex-lb SwiftBar](https://github.com/joschi655/codex-lb-swiftbar) —— 只读的 SwiftBar/Bun 监控器，显示账户池状态与配额余量

## 开发

```bash
# Docker
docker compose watch

# 本地
uv sync && cd frontend && bun install && cd ..
uv run codex-lb                              # 后端 :2455
cd frontend && bun run dev                   # 前端 :5173

# Nix
nix run .
nix develop # 进入开发环境
```

## 贡献者 ✨

完整的贡献者名单请参见英文 [README](./README.md#contributors-) 中由 [all-contributors](https://github.com/all-contributors/all-contributors) 自动生成的列表（该列表由机器人维护，仅写入 `README.md`）。该项目欢迎任何形式的贡献！
