# Context

## Purpose

Codex reads its quota from `<chatgpt_base_url>/wham/usage`. With no
`chatgpt_base_url` set, that is chatgpt.com, and the answer covers only the
account Codex is logged into. codex-lb routes model turns across a pool, so the
status line can disagree with what the pool is doing.

Setting `chatgpt_base_url` to codex-lb's `/backend-api` fixes that. codex-lb
answers `wham/usage` with pooled usage, and forwards the other ChatGPT-backend
calls Codex makes (account checks, user settings, plugins, cloud tasks) so they
keep working. The behavior is opt-in on the client: nothing changes unless the
client is pointed at codex-lb, and there are no new settings.

Requirements live in [spec.md](spec.md).

## Example

Two accounts are pooled, at 25% and 6% used. The Codex TUI `/status` shows:

| Client setting | 5h limit shown |
|---|---|
| no `chatgpt_base_url` | 75% left (the logged-in account only) |
| `chatgpt_base_url = ".../backend-api"` | 85% left (pooled) |

Codex then calls `GET /backend-api/wham/accounts/check` with its own bearer
token and `chatgpt-account-id`. codex-lb confirms the token belongs to that
account, then sends the request to upstream `/backend-api/wham/accounts/check`
with the caller's token, through the account's configured egress route.

## What Codex sends

Captured from Codex 0.157.0 with `chatgpt_base_url` ending in `/backend-api`:

| Request | Bearer and account id |
|---|---|
| `GET /backend-api/wham/accounts/check` | yes |
| `GET /backend-api/wham/settings/user` | yes |
| `GET /backend-api/ps/plugins/installed`, `ps/plugins/list`, `ps/plugins/suggested/codex` | yes |
| `GET /backend-api/plugins/featured?platform=codex` | yes |
| `POST /backend-api/codex/analytics-events/events` | yes |
| `POST /backend-api/ps/mcp` | no |

Every path equals the chatgpt.com path, so forwarding is an identity mapping
with no path table. A fixture test pins these paths so a change in Codex shows
up as a failing test.

## Decisions

- **Only the `/backend-api` base-URL style is supported.** With a root URL,
  Codex splits its calls between `/api/codex/...` and bare root paths
  (`/ps/plugins/...`, `/plugins/featured`). A catch-all over those would sit on
  codex-lb's own root namespace. `/api/codex/usage` keeps working for existing
  setups.
- **The caller's identity is used, never a pool account's.** Settings, plugins
  and cloud tasks are per-user. Answering them from a pool account would show
  one person another account's data and could land writes on the wrong account.
  The matched pool account supplies only the egress route.
- **Identity is verified before egress.** The passthrough runs the same check
  as `/api/codex/usage`: active account lookup, route resolution, and an
  upstream `/wham/usage` call with the caller's token. A local-only check would
  let anyone who knows an active account id send arbitrary calls out through
  that account's route with a made-up token. The confirmed token/account
  binding is cached for 60 seconds, keyed by a hash of both. Every call still
  rechecks from the database that the account is active and resolves its
  current route.
- **Ineligible requests do not match the route.** The catch-all route class
  declines them in `matches()`, so they keep their existing 404/405 envelopes.
  Matching and then raising 404 would turn a 405 into a 404, and an anonymous
  probe would get a 401 that reveals the passthrough.
- **Pool-routed namespaces are closed.** Unserved paths under `codex/`,
  `files` and `transcribe` are never forwarded. A new Codex model endpoint must
  not silently bypass the pool and bill the caller's own account, and file ids
  are pinned to pool accounts.
- **The raw path is forwarded.** The decoded path parameter would turn
  `settings%2Fdetail` into `settings/detail`. The path is taken from the ASGI
  `raw_path`, with any `root_path` prefix removed, and the request gets a 400 if
  the raw and decoded forms disagree. The decline checks still run on the
  decoded form, which is the stricter view.
- **Mount prefixes are handled per server.** uvicorn puts the literal
  `--root-path` in `raw_path`; a Starlette `Mount` leaves the client's encoded
  prefix there; `httpx.ASGITransport` omits it. The prefix is stripped by a
  literal match first, then by walking segment boundaries and unquoting.
- **Responses stream.** The upstream helper returns status, headers and an
  async body iterator, and releases the upstream response and route lease when
  the body finishes, fails or is cancelled. There is no total timeout, since
  event streams can be long-lived. `Cookie` is dropped from the request,
  `Accept-Encoding: identity` is set, and response headers pass an allowlist
  that excludes `Set-Cookie`. Redirects are relayed, not followed.
- **Aliases reuse the existing handlers.** `/backend-api/wham/usage` and
  `/backend-api/wham/rate-limit-reset-credits/consume` register the same
  handler functions as their `/api/codex` twins, so auth and payload cannot
  drift.

## Constraints

- Connectors (`/backend-api/ps/mcp`) do not work with this setting. Codex's MCP
  client sends no `Authorization` and no `chatgpt-account-id` to any
  non-chatgpt.com host. codex-lb has no caller identity to forward and will not
  use a pool account's, so it answers as for any unserved path and Codex logs
  an MCP worker failure and carries on. Fixing it needs a Codex change.
- The Codex account must also be in the pool. Otherwise forwarded calls return
  401.
- Forwarded calls are not model traffic. They are not request-logged and use no
  pool quota.
- Forwarded calls that write user state (for example creating a task) act on
  the caller's own account under the caller's own token, as they would without
  codex-lb.
- Codex 0.157.x runs TUI sessions through a shared background app-server
  daemon that reads `config.toml` only at start. After adding
  `chatgpt_base_url`, run `codex app-server daemon restart` (it interrupts
  running sessions), or pass the setting with `-c`, which applies at once.
- Rollback is removing the `chatgpt_base_url` line. There is no schema or
  settings change.

## Failure modes

- Unknown or inactive account, or missing `chatgpt-account-id`: 401, no
  upstream call.
- Token not accepted for the account: rejected before the forward, no egress
  through the account's route.
- Upstream rejects the forwarded call (401 and similar): relayed as is. The
  pool account's health is not touched.
- Account bound to an upstream proxy pool whose route cannot be resolved: 503
  `upstream_error`, with no direct-egress fallback.
- Raw and decoded paths disagree, or the raw path lacks the `/backend-api/`
  prefix: 400, nothing forwarded.
- `sk-clb-` bearer, closed namespace, `..` or encoded traversal, request
  without a ChatGPT bearer: the route does not match, and the request gets the
  normal 404 or 405.
- Client disconnects mid-stream: the upstream connection and route lease are
  closed.

## Operations

Each forwarded call logs one stream-end record (method, path, account, status,
outcome, duration, bytes), at WARNING for `error` or 5xx. Declined requests and
identity checks log at DEBUG with the reason.
