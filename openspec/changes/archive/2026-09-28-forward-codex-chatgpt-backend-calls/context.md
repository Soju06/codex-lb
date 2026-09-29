# Context

## How this came up

Two Plus accounts are pooled. One was at 96% of its 5-hour window and the other
at 9%. New Codex sessions routed correctly to the 9% account (request logs and
`Selected account_id=...` log lines confirm it), but Codex's status line kept
showing "4% left". Codex was logged in as the 96% account and had no
`chatgpt_base_url` set, so it read its quota straight from
`chatgpt.com/backend-api/wham/usage` for its own login. codex-lb never saw a
usage request.

The same token against both endpoints on 2026-09-27:

| Endpoint | 5h used | Weekly used |
|---|---|---|
| `chatgpt.com/backend-api/wham/usage` | 96% | 18% |
| `<codex-lb>/api/codex/usage` | 52% (pooled) | 15% |

## What Codex actually sends

Captured from Codex 0.157.0 (`codex exec`) against a local logging server,
2026-09-27. Header names only; no token values were recorded.

With `chatgpt_base_url = "http://<capture>/backend-api"`:

| Request | Bearer + account id |
|---|---|
| `GET /backend-api/wham/accounts/check` | yes |
| `GET /backend-api/wham/settings/user` | yes |
| `GET /backend-api/ps/plugins/installed?...`, `ps/plugins/list?...`, `ps/plugins/suggested/codex?...` | yes |
| `GET /backend-api/plugins/featured?platform=codex` | yes |
| `POST /backend-api/codex/analytics-events/events` | yes |
| `POST /backend-api/ps/mcp` | **no** |

Every path is exactly the chatgpt.com path. With the root style
(`chatgpt_base_url = "http://<capture>"`) the same calls were split between
`/api/codex/...` and bare root paths (`/ps/plugins/...`, `/plugins/featured`,
`/codex/analytics-events/events`). That is why the change supports only the
`/backend-api` style.

## Why the first design was replaced

The first implementation forwarded `/api/codex/<rest>` to `/wham/<rest>`,
based on path pairs found in the Codex binary. Fake-upstream tests passed
because they encoded the same assumption. The live deploy found two problems:
- ChatGPT serves MCP at `/backend-api/ps/mcp` and answers `404` at
  `/backend-api/wham/ps/mcp`.
- The plugin calls never reach `/api/codex/` at all.

The capture above settled the design.

## Why connectors stay unavailable

Codex's MCP client (`codex-mcp-client/0.157.0`) sends no `Authorization` and no
`chatgpt-account-id` when the host is not chatgpt.com. That is a reasonable
safety choice on Codex's side. codex-lb has no caller identity to forward, and
using a pool account's token would show one user another account's connectors.
So codex-lb answers `401`, Codex logs the MCP worker failure and carries on,
and connectors are unavailable while `chatgpt_base_url` points at codex-lb.

## Why the caller's identity and not a pool account

Everything behind these paths is per-user: settings, plugins, cloud tasks.
Answering them with a pool account would show one person another account's
data, and would let writes land on the wrong account. Model traffic is
different: it is fungible, which is why the pool exists.

## Live verification (2026-09-27)

Deployed to the home instance and checked with the real Codex client, pointed
at `https://<codex-lb-host>/backend-api`.

A `codex exec` session produced these backend calls through codex-lb:

| Call | Result |
|---|---|
| `wham/accounts/check`, `wham/settings/user` | 200 (forwarded) |
| `ps/plugins/list` (22 pages), `ps/plugins/installed` (x2), `ps/plugins/suggested/codex`, `plugins/featured` | 200 (forwarded) |
| `POST ps/mcp` (no credentials) | 405, the unserved-path answer |
| model turns | routed through the pool as before |

The Codex TUI `/status` with two accounts at 25% and 6% used:

| Setting | 5h limit shown |
|---|---|
| no `chatgpt_base_url` | 75% left (the logged-in account only) |
| `chatgpt_base_url = ".../backend-api"` | 85% left (pooled) |

## Codex app-server daemon caches config

After `chatgpt_base_url` was added to `~/.codex/config.toml`, new TUI sessions
still showed 75% (the logged-in account alone). A `-c` override showed 85%
(pooled), and both sessions' websockets landed on the same pool account. The
cause: Codex 0.157.1 runs TUI sessions through a shared background app-server
daemon (`codex app-server daemon ...`), which loads `config.toml` at start. A
`-c` flag reaches the session directly. After a config edit, the daemon needs
`codex app-server daemon restart` (it interrupts running sessions). The client
docs say so.
