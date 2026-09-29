## Context

See proposal.md for why. Three existing pieces shape the approach:

- `usage_router` (no prefix, no proxy-API-key guard) serves `/api/codex/usage`
  and `/api/codex/rate-limit-reset-credits/consume`. Its auth is the caller's
  ChatGPT identity (`validate_codex_provider_usage_identity`), not a codex-lb
  API key; `api-keys` spec keeps `/api/codex/usage` out of the API-key guard.
- `validate_codex_usage_identity` maps `chatgpt-account-id` to an active pool
  account, resolves that account's upstream route, then calls upstream
  `/wham/usage` with the caller's token to prove the token is real.
- `_codex_control_proxy` / `codex_control_request` forward Codex control calls
  upstream, but under a **pool-selected** account and with a **buffered** body.
  Neither fits here: these calls are per-user, and responses can stream.

codex-lb already owns four `/backend-api` namespaces: `codex/*` (model traffic,
pool-routed), `wham/agent-identities/jwks`, `transcribe`, and `files/*`.

## Goals / Non-Goals

**Goals:**
- With `chatgpt_base_url` set to codex-lb's `/backend-api`, Codex shows pooled
  quota and every backend feature that can work through a proxy keeps working.
- No pool credential is ever used for a caller's per-user backend call.

**Non-Goals:**
- Connectors (`/backend-api/ps/mcp`). Codex 0.157.0 sends that call without
  credentials to any non-chatgpt.com host (see context.md); fixing it needs a
  Codex change, not a codex-lb one.
- The root-URL style (`chatgpt_base_url = "https://<codex-lb>"`). In that
  style Codex splits its calls between `/api/codex/*` and bare root paths
  (`/ps/plugins/*`, `/plugins/featured`, `/codex/analytics-events/*`), which
  would put a catch-all over codex-lb's own root namespace. `/api/codex/usage`
  keeps working for existing setups; nothing else is added there.
- Changing `/api/codex/usage` output, or rewriting forwarded responses.
- Request logging or quota accounting for forwarded calls. They are not model
  traffic and consume no pool quota.

## Decisions

**Support the `/backend-api` base-URL style only.** A capture of Codex 0.157.0
against a logging server shows that with `chatgpt_base_url` ending in
`/backend-api`, every request path equals its chatgpt.com path. The
passthrough is therefore an identity mapping, with no path table and no
per-path exceptions. The earlier `/api/codex` design needed a `/wham/` rewrite
plus a special case for `ps/mcp`, and it still missed the root-level plugin
paths. *Alternative:* keep `/api/codex` and add root forwarding. Rejected for
the reasons under Non-Goals.

**Aliases reuse the existing handlers.** `/backend-api/wham/usage` and
`/backend-api/wham/rate-limit-reset-credits/consume` are registered with
`add_api_route` against the same handler functions as their `/api/codex`
twins, so auth, payload, and cache invalidation cannot drift apart. They live
on the passthrough router, which has no proxy-API-key dependency, rather than
on `wham_router`, which has one.

**Catch-all router included after every other `/backend-api` router.** One
`/backend-api/{rest:path}` route for the six methods in
`app/modules/proxy/codex_backend_passthrough.py`, included in `app/main.py`
after `usage_router`, which comes after all `/backend-api` routers. First-match
order keeps every served route authoritative. The catch-all is HTTP only, so
the websocket routes are unaffected.

**Ineligible requests do not match the route at all.** The catch-all uses an
`APIRoute` subclass whose `matches()` declines closed namespaces, escaping
paths, requests without a ChatGPT bearer, `sk-clb-` principals, and capability
carriers. Starlette then falls through to codex-lb's normal unmatched-path
handling, so those requests keep their exact prior 404/405 envelopes. The
repo's envelope-parity tests (`/backend-api/` root, `/backend-api/codex/...`
trailing slashes, compact) pin that. *Alternative:* match and then raise 404.
Rejected because a POST that used to get 405 would get 404, and an anonymous
probe would get a 401 that reveals the passthrough.

**Pool-routed namespaces are closed, not forwarded.** Unserved paths under
`codex/`, `files`, and `transcribe` never match. A future Codex model endpoint
that codex-lb does not know yet must not silently bypass the pool and bill the
caller's own account, and file ids are pinned to pool accounts.

**Confined path.** The route declines `rest` that is empty, starts with `/`,
has `.` or `..` segments, or contains `?`, `#`, `\`, or control characters,
so the forward cannot leave upstream `/backend-api/`.

**Verified identity gate, cached binding.** Before forwarding, the passthrough
runs the same verification `/api/codex/usage` uses
(`_verify_codex_caller_identity`: active account lookup, route resolution,
upstream `/wham/usage` with the caller's token, workspace remap). The
confirmed token/account binding is cached for 60 seconds, keyed by a hash of
the account id and the token, so a Codex session pays one extra upstream round
trip per minute rather than one per call. Only the binding is cached: every
call still rechecks from the database that the account is active and resolves
its current egress route. Rejections on the forwarded call
itself are relayed and never recorded as account health.

*Earlier choice, revised:* the first version ran only the local half (account
lookup and route resolution) and let upstream authenticate the token on the
forwarded call, to avoid the extra round trip. Review pointed out that this
let anyone who knows an active account's `chatgpt-account-id` send arbitrary
`/backend-api` calls out through that account's egress with a made-up token.
With verification first, the only pre-verification egress is the fixed usage
check `/api/codex/usage` already makes. *Alternative not taken:* verify the
token's JWT signature locally against OpenAI's published keys. That removes
the pre-verification request entirely, but adds a key-fetching and
claims-parsing dependency the rest of codex-lb does not have.

**Caller token, account route.** The upstream request is built with the
caller's `Authorization` and `chatgpt-account-id`. The matched pool account
contributes only its egress route. This mirrors how
`_consume_rate_limit_reset_credit_for_request` already uses
`codex_usage_identity_access_token` + `codex_usage_identity_route`.

**Streaming client helper.** `app/core/clients/codex_backend.py` is a
streaming counterpart of `codex_control_request`. It returns status, headers,
and an async body iterator, and releases the upstream response and route lease
when the body finishes, errors, or is cancelled. Headers:
- Request headers go through `_build_upstream_headers`, with `Cookie` dropped
  and `Accept-Encoding: identity` set. That way no transport (aiohttp or
  native egress) ever holds a compressed body it would have to relabel.
- Response headers pass an allowlist: the control-proxy set plus
  `mcp-session-id`, `retry-after`, and `www-authenticate`. This drops
  `Set-Cookie` and edge headers.

Timeouts: connect timeout comes from settings, and the stream idle timeout
applies. There is no total timeout, because streams can be long-lived. A
`StreamingResponse` subclass also closes upstream in a `finally` around
`__call__`, since the body generator never runs when the response fails before
its first chunk, or for HEAD.

**API-key principals fall through, not to a pool fallback.** An `sk-clb-`
caller has no per-user ChatGPT identity to forward, and substituting a pool
account would leak that account's data.

**Aliases join the capability-route inventory.** The `wham` aliases reuse
`api.py` handlers, so `test_daybreak_capability_routes` classifies them next
to their `/api/codex` twins: usage is local-authenticated, and consume is
fail-closed.

## Risks / Trade-offs

- [Codex adds a backend path under a closed namespace] → It answers 404 as it
  does today; adding a served route is a deliberate change.
- [Forwarded path writes user state upstream (e.g. `POST tasks`)] → It is the
  caller's own account acting under the caller's own token, the same as
  without codex-lb.
- [Long-lived streams hold a connection and route lease] → Released on client
  disconnect; the integration test covers it.
- [Codex changes its path layout] → A fixture test pins the paths captured
  from 0.157.0 so drift shows up as a failing test.

## Migration Plan

No schema or settings change. Deploy normally. Operators opt in on the client
side by setting top-level
`chatgpt_base_url = "https://<codex-lb-host>/backend-api"` in Codex's
`config.toml`, or per run with `-c`. Rollback: remove that line.
