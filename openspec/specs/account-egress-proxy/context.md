# account-egress-proxy — Context

## Purpose

Operators routinely pool many ChatGPT accounts behind a single `codex-lb`
deployment. Without per-account egress, every account exits the network
from the same IP, which causes shared-IP rate limiting and observable
fingerprinting (IP, ASN, geo). This capability lets each account
optionally route its outbound traffic through a dedicated SOCKS5 proxy so
each account behaves like an isolated tenant.

## Decisions and rationale

- **Structured fields, not a URL**. The configuration is stored as
  `proxy_host`, `proxy_port`, `proxy_username`, `proxy_password_encrypted`,
  `proxy_remote_dns`, and `proxy_label` rather than a single
  `socks5://user:pass@host:port` URL. The structured form keeps the
  password encryption boundary clean (separate column, encrypted at rest
  with the existing `TokenEncryptor`), avoids URL escaping pitfalls in
  exotic credentials, and lets the dashboard render a typed summary
  without parsing the URL.
- **Save-time end-to-end probe (real OAuth refresh)**. We deliberately
  reject lighter probes (TCP CONNECT, HEAD on a benign endpoint) because
  they can't distinguish "proxy is reachable but OAuth is rejecting this
  account" from "proxy is good and account is healthy". The end-to-end
  refresh exercises proxy negotiation + TLS + upstream auth in one step,
  which means the typed `ProbeResult.reason` (`proxy_connect`,
  `proxy_auth`, `tls`, `upstream_status`, `invalid_response`, `timeout`)
  is enough for the UI to render a precise message.
- **Import-time proxy setup is atomic from the account's perspective**.
  When the dashboard imports `auth.json` with proxy fields, the backend
  probes through that proxy before the account row is committed. If the
  probe fails, no account is inserted; if it succeeds, rotated OAuth
  tokens and proxy fields are committed together before import-time usage
  refresh is allowed to run. The service also evicts the account's cached
  egress client before that refresh so overwrite imports cannot reuse a
  stale direct or previous-proxy session.
- **Per-account pooled `aiohttp.ClientSession`**. Each proxy-configured
  account gets a single managed session backed by
  `aiohttp_socks.ProxyConnector`. Pooling preserves keepalive and sticky
  semantics; switching to per-request connectors would make first-token
  latency observable to the user. Accounts without a proxy transparently
  share the global session, so the existing fast path is preserved.
- **No failover**. If a proxy goes bad mid-traffic, the runtime failure
  tracker deactivates the account (`deactivation_reason="proxy_unreachable"`)
  rather than silently failing back to direct egress. Operators
  explicitly accepted this tradeoff: tenant isolation matters more than
  availability for this feature, and a silent fallback would leak traffic
  to the host's default IP.
- **Process-local failure tracker, idempotent transition**. The
  `ProxyFailureTracker` lives in memory and uses
  `AccountsRepository.update_status_if_current(...)` to make the
  ACTIVE→DEACTIVATED transition idempotent across replicas. Each replica
  that observes the same failure pattern reaches the threshold
  independently and triggers the same idempotent write — we accept one
  redundant DB write per replica to avoid coordinating a distributed
  counter.
- **`socks5h` by default**. `proxy_remote_dns=true` (the default) makes
  the `aiohttp_socks.ProxyConnector` resolve the upstream hostname at the
  proxy. This avoids a class of DNS leaks on hosts where the local
  resolver would expose the upstream hostname to a network operator.

## Failure modes

| Symptom                                  | Reason                | UI message (frontend)                                  |
|------------------------------------------|-----------------------|--------------------------------------------------------|
| Cannot reach the SOCKS5 endpoint at all  | `proxy_connect`       | "Could not reach the proxy. Check the host/port…"      |
| SOCKS5 username/password rejected        | `proxy_auth`          | "The proxy rejected the username or password."        |
| TLS handshake to upstream failed         | `tls`                 | "TLS handshake to the upstream failed through the proxy." |
| Upstream OAuth returned non-2xx          | `upstream_status`     | "Upstream rejected the refresh. Re-authenticate first." |
| Probe exceeded the 10s budget            | `timeout`             | "The probe timed out. Increase the timeout or check…"  |
| 3 proxy-level errors in 60s at runtime   | account `DEACTIVATED` | Dashboard shows the deactivated badge with reason.    |

## Operational notes

- **Recovering a deactivated account**: the operator clears the proxy
  via the dashboard (`DELETE /api/accounts/{id}/proxy`) or reconfigures
  it (`POST /api/accounts/{id}/proxy`). Both flows call
  `invalidate_account_client(account_id)`, which evicts the cached
  session and resets the runtime failure tracker's rolling window so
  the account starts with a clean slate. Reactivation
  (`POST /api/accounts/{id}/reactivate`) does the same and is the
  manual unblock path when the proxy itself was healthy and an upstream
  hiccup tripped the threshold.
- **Settings tunables**:
  - `account_proxy_probe_timeout_seconds` (default 10s) — save-time probe budget
  - `account_proxy_failure_threshold` (default 3) — runtime tracker
  - `account_proxy_failure_window_seconds` (default 60) — runtime tracker

## Concrete example

```json
POST /api/accounts/acc_123/proxy
{
  "host": "house-proxy-1.internal",
  "port": 1080,
  "username": "house",
  "remoteDns": true,
  "label": "house-1"
}
```

On success (200):

```json
{
  "host": "house-proxy-1.internal",
  "port": 1080,
  "username": "house",
  "hasPassword": true,
  "remoteDns": true,
  "label": "house-1",
  "lastValidatedAt": "2026-05-23T12:00:00Z"
}
```

On probe failure (422):

```json
{
  "error": {
    "code": "proxy_probe_failed",
    "message": "Username and password authentication failure",
    "reason": "proxy_auth"
  }
}
```

## Related capabilities

- [`outbound-http-clients`](../outbound-http-clients/spec.md) — the rule
  that account-bound calls MUST egress through the per-account session
  when configured.
