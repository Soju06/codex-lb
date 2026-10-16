## Why

Codex CLI reads the limits it displays in `/status` (and the footer) from
`chatgpt_base_url`, which defaults to `https://chatgpt.com/backend-api`. Behind
codex-lb that number describes the single account in the client's local
`auth.json`, not the pool the balancer is actually rotating through — a user
whose local account is exhausted sees `5h 0% left` while four healthy accounts
serve every turn. codex-lb already answers this correctly: `GET /api/codex/usage`
returns the capacity-weighted usage of the active pool in Codex's native
payload, and Codex calls exactly that path when `chatgpt_base_url` is set to
codex-lb's origin (no `/backend-api` segment).

The same base URL also carries Codex's plugin catalog reads —
`/ps/plugins/list|installed|search|suggested/*|workspace/*`, the per-plugin
detail read before an install, and `/plugins/featured`. Today codex-lb has no
route for them, so the SPA fallback answers with the dashboard HTML: Codex
parses that as a catalog failure and the remote marketplace
(`openai-curated-remote`) silently disappears. Users must choose between
correct limits and a working marketplace.

## What Changes

- Serve `GET /plugins/featured` and `GET /ps/plugins/{path}` at the origin
  root and forward them upstream as Codex control requests, preserving
  method, query string, and the upstream status/body/allowlisted headers
  verbatim. They are served with pool credentials and session affinity like
  the other control requests, not with the caller's bearer token.
- Keep the upstream `codex/` namespace prefix off these paths: `ps/` and
  `plugins/` join `wham/` as root-level upstream namespaces.
- Treat `ps/` and `plugins/` as API namespaces in the SPA fallback so an
  unknown path there returns the OpenAI-style 404 envelope, never HTML.
- Gate the new surface behind the IP firewall allowlist together with the
  other proxy surfaces, since it spends pool credentials upstream.
- Document `chatgpt_base_url = "http://127.0.0.1:2455"` in the Codex client
  setup page as the way to get pool-wide limits in `/status`.

No new settings. The passthrough is GET-only: every catalog call Codex 0.154
makes, including the detail read that precedes an install, is a GET.

## Capabilities

### New Capabilities
- `codex-plugin-catalog-passthrough`: Codex plugin-catalog reads issued
  against `chatgpt_base_url` are forwarded upstream verbatim so pointing that
  URL at codex-lb keeps the remote marketplace working.

### Modified Capabilities
- `api-firewall`: the plugin-catalog passthrough joins the firewall-protected
  proxy paths.
