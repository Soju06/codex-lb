# Codex Usage Reset Context

Codex CLI 0.142.x reads reset-credit availability from the top-level
`rate_limit_reset_credits` object returned by `/api/codex/usage`. When the user
chooses to redeem a reset, the client posts `redeem_request_id` to
`/api/codex/rate-limit-reset-credits/consume`.

codex-lb should not maintain its own earned-reset balance. Upstream ChatGPT is
the authority for both availability and consume outcome. codex-lb only validates
that the caller's ChatGPT bearer token is tied to a registered active account,
forwards the request through the same upstream route policy as usage validation,
and refreshes local usage after a reset succeeds.

The dashboard accounts page should also treat upstream as authoritative. The
selected account detail view can fetch the selected account's current
`rate_limit_reset_credits.available_count` directly from upstream usage using
the stored account tokens and existing upstream route policy, rather than adding
local reset-credit storage.

Usage panel reset actions intentionally consume an upstream reset credit first,
then use a usage-only force-refresh path: the operator confirms the Reset
action, codex-lb posts a generated `redeem_request_id` upstream using the
selected account tokens, codex-lb fetches upstream usage for the selected
account, and the dashboard invalidates account-related queries. It does not
send model probe traffic.

The dashboard should not make reset updates feel immediate by permanently
shortening polling or adding a reset-credit polling loop. Freshness comes from
explicit reset actions and query invalidation after successful writes.

Example consume payload:

```json
{ "redeem_request_id": "2f8c5d19-6c54-4f92-98a2-b20166f44ed8" }
```

Example upstream response preserved by codex-lb:

```json
{ "code": "reset", "windows_reset": 2 }
```
