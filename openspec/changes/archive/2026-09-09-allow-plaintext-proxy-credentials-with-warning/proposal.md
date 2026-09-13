## Why

#1995 made route resolution reject any `http://`, `socks5://`, or `socks5h://` proxy endpoint that carries a username or password (`plaintext_proxy_credentials_forbidden`), and the dashboard refused to create such endpoints. The rule came from a CodeRabbit security finding (CWE-319, rated internal reachability and moderate/difficult exploitability) that was resolved by hard rejection instead of a risk decision. In practice most commercial egress proxies authenticate with Basic or SOCKS5 credentials over a plaintext hop and do not offer a TLS proxy port; the production deployment of 1.25.0-beta.2 on 2026-09-07 failed every routed request for ten minutes because both configured endpoints match that shape and had to be rolled back.

The exposure is the proxy account credential on the LB-to-proxy hop only; upstream traffic itself is always TLS to the target. The maintainer decision is to allow these endpoints and make the trade-off visible instead of blocking them.

## What Changes

- Route resolution accepts credential-bearing `http://`, `socks5://`, and `socks5h://` endpoints. It logs one credential-free warning per endpoint per process naming the endpoint id, scheme, host, and port.
- The upstream proxy admin API exposes `plaintextCredentials` on every endpoint (true when the scheme is not `https` and the endpoint has a username or password); endpoint creation no longer rejects such payloads.
- The settings dashboard renders a warning under each flagged endpoint telling the operator the credentials are sent unencrypted over that scheme and recommending an `https://` proxy or a credential-free IP allowlist.
- `ResolvedProxyEndpoint` keeps delivering credentials to aiohttp through `Proxy-Authorization` on the CONNECT tunnel (TLS targets) and to the SOCKS connector through its username/password parameters; the target-side guard (`_reject_credentialed_plaintext_target`, credentialed proxies require an https/wss upstream target) is unchanged.
- Additive wire-format change: the upstream proxy admin API's endpoint objects (creation response and `GET /api/settings/upstream-proxy` listing) gain a boolean `plaintextCredentials` field. Existing fields, request payloads, configuration, and the persistence schema are unchanged; the dashboard schema reads the field with a `false` default so older servers still render.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: Credential-bearing plaintext proxy endpoints are permitted; the requirement to reject them is replaced by the requirement to flag them.
- `upstream-proxy-routing`: Route resolution warns once per plaintext-credential endpoint and the admin API exposes the flag.
- `frontend-architecture`: The upstream proxy settings section shows the plaintext-credential warning per endpoint.

## Impact

Affected code: `app/core/upstream_proxy/{types,resolver}.py`, `app/modules/settings/{api,schemas}.py`, the settings frontend (schema, endpoint list, i18n en/ko/zh-CN, MSW mock), and the corresponding unit/integration/component tests. Operators with existing plaintext-credential endpoints regain routed egress on upgrade and see the warning in the dashboard.
