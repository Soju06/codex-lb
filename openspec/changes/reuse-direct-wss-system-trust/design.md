## Context

`_connect_upstream_websocket` selects routed/native transport first, then calls Python `websocket_connect` without an explicit TLS context. For `wss://`, websockets17.1 asks the event loop to create a default system-trust context per connection. Phase-one real TLS probes observed10 builds for10 separate opens, versus1 for10 retained turns. Main's private aiohttp `_shared_ssl_context` adds certifi and is a different trust policy.

## Goals / Non-Goals

**Goals:** Reuse the existing Python WSS verification policy across separate connections and client rotations; preserve cancellation, scheme behavior and verification.

**Non-Goals:** Change certifi/system roots, proxy TLS, native egress, account routing, WebSocket policy, global CodexClient ownership, or application concurrency. No configuration or dependency.

## Decisions

- Own a private memoized system-default constructor in `app/core/clients/http.py`, beside the existing aiohttp lifecycle. Construct it with `ssl.create_default_context()` only; do not call the certifi builder or mutate a returned context.
- Warm the system context during normal outbound-client initialization and reuse it across refreshes. Reset it at `close_http_client`, alongside the current cache reset, so close/reinitialize observes changed trust inputs. A direct caller before initialization may populate the same cache lazily. This avoids per-handshake loading without a new lifecycle owner or background task.
- Supply it only to the Python fallback's `wss://` server-TLS argument. Omit the argument for `ws://`; preserve all proxy/subprotocol/compression/timeout arguments and error/cancellation ownership. Leave HTTPS-proxy-specific TLS defaults untouched.
- Use the existing HTTP client context/lifecycle tests and Python WS client tests. A real local TLS origin witnesses trusted success, untrusted and wrong-host rejection. Repeated separate opens must reuse one context; reuse must fail when the memoization or call-site parameter is removed. Compare verification policy with a fresh default context, not the certifi-augmented context.

## Risks / Trade-offs

- [Trust inputs no longer reload per open] → Document lifecycle reset/restart behavior; keep system environment and hostname-verification semantics.
- [Accidentally sharing aiohttp's broader root bundle] → Distinct private constructor and real verification/policy checks.
- [Test-only cache leakage] → Extend existing reset fixtures, with close/reinitialize coverage.
- [Benefit overstated] → Cost applies to Python connection churn, not every retained model turn; local probes do not explain historical long waits.

## Migration Plan

No data migration. Ordinary process restart activates the change. Rollback restores per-open system context construction without changing persisted state.

## Open Questions

None blocking implementation. The private helper name is an implementation detail; keep it within the existing outbound-client owner.
