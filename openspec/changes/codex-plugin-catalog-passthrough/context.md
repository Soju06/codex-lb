# codex-plugin-catalog-passthrough — context

## Purpose and scope

Let a Codex CLI user set `chatgpt_base_url = "http://127.0.0.1:2455"` — which
is what makes `/status` show the pool's limits via `GET /api/codex/usage` —
without losing the remote plugin marketplace that Codex fetches from the same
base URL. In scope: the read-only catalog paths Codex 0.154 issues. Out of
scope: the `Account:` line (it is the local `auth.json` identity and stays so),
and `GET /api/codex/rate-limit-reset-credits`, which Codex also calls from this
base to show its "usage limit resets available" hint and which codex-lb does not
serve yet.

## How the two path families were established

Observed with a throwaway HTTP listener standing in for `chatgpt_base_url`,
driving `codex app-server` (no model tokens spent):

| `chatgpt_base_url`                  | usage path Codex calls          |
|-------------------------------------|---------------------------------|
| `http://host` (origin)              | `GET /api/codex/usage`          |
| `http://host/backend-api`           | `GET /backend-api/wham/usage`   |

With the origin form, the catalog reads land on the same host as
`/ps/plugins/list?scope=GLOBAL&limit=200`, `/ps/plugins/installed?limit=200`
(also with `includeDownloadUrls=true`), `/ps/plugins/suggested/codex?scope=GLOBAL`,
`/plugins/featured?platform=codex`, and — when installing from the remote
marketplace — `GET /ps/plugins/{name}?includeDownloadUrls=true` before the
download. The Codex binary also carries `/ps/plugins/search` and
`/ps/plugins/workspace/{created,shared}`; the `{path}` route covers them.

## Decisions

- **Origin-root router, not `/backend-api/…`.** Codex appends these paths to
  `chatgpt_base_url` directly, so they must exist where the client puts them.
- **Forward as a Codex control request with pool credentials**, exactly like
  `thread/goal/*` and `agent-identities/jwks`, rather than replaying the
  caller's bearer token. Under codex-lb the local `auth.json` token is not
  refreshed by the balancer's guardian and goes stale; pool credentials are
  the ones codex-lb keeps alive. Session affinity keeps one session on one
  account, so the `installed` view stays consistent within a session.
- **GET only.** Every catalog call observed is a GET. Writes are refused with
  405 so a future upstream write is a deliberate decision, not an accidental
  passthrough of pool credentials.
- **Root-level upstream namespaces.** `codex_control_request` prefixes paths
  with `codex/`; `wham/` was already exempt and `ps/` / `plugins/` join it,
  because upstream serves them at `backend-api/ps/…` and
  `backend-api/plugins/…`.
- **Firewall-protected.** The surface spends pool credentials upstream, so it
  is a proxy surface and sits behind the IP allowlist with `/backend-api/codex`
  and `/v1`.

## Known limitation

`/ps/plugins/installed` answers for whichever pool account served the request,
not for the user's local identity. Codex keeps its own local install state
(the `openai-curated` marketplace cache) and treats that as authoritative, so
this only affects the upstream "installed" flag on catalog entries.

## Failure modes

- Upstream 4xx/5xx: surfaced through the existing control-request error path
  (`ProxyResponseError` → logged OpenAI-style error), like other control routes.
- Unknown path under `/ps/` or `/plugins/`: OpenAI 404 envelope from the SPA
  fallback's excluded-prefix rule, so Codex fails fast instead of parsing HTML.
- No active account: the control request fails as it does for every other
  control route; Codex shows the remote marketplace as unavailable.

## Example

```toml
# ~/.codex/config.toml (top level, before any [section])
chatgpt_base_url = "http://127.0.0.1:2455"
```

`GET http://127.0.0.1:2455/ps/plugins/list?scope=GLOBAL&limit=200` →
forwarded to `https://chatgpt.com/backend-api/ps/plugins/list?scope=GLOBAL&limit=200`
with a pool account's credentials; status, body, and `content-type` /
`cache-control` come back verbatim.
