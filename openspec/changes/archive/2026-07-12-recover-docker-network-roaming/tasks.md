## 1. Network Failure Classification and Client Recovery

- [x] 1.1 Add typed DNS/host-route failure classification with credential-safe routed-code preservation.
- [x] 1.2 Add compare-and-swap/coalesced shared HTTP client rotation for process-wide network failures.
- [x] 1.3 Mark classified process-wide failures account-neutral and add unit coverage proving health state is unchanged.
- [x] 1.4 Keep failed or cancelled shared-client replacement cleanup safe without replacing the current generation.

## 2. Responses and WebSocket Recovery

- [x] 2.1 Retry only proven pre-dispatch HTTP/SSE Responses attempts on the same account within the existing deadline.
- [x] 2.2 Retry upstream WebSocket opens centrally on the same account within the existing deadline.
- [x] 2.3 Add regression tests for recovery, timeout bounding, continuity-owner preservation, and coalesced diagnostics.
- [x] 2.4 Recover token-refresh network failures on the same account without health penalties or failover.
- [x] 2.5 Surface post-dispatch send, receive, and serialized terminal failures account-neutrally without replay.

## 3. Docker Deployment and Operations

- [x] 3.1 Update portable standalone Docker examples to use an idempotently created user-defined bridge without presenting it as a network-switching guarantee.
- [x] 3.2 Declare and test user-defined default bridges in stock Compose deployments without hard-coded public DNS.
- [x] 3.3 Add OpenSpec context with rationale, failure modes, diagnostics, and a concrete host-versus-container DNS check.
- [x] 3.4 Attach the current local codex-lb container to the dedicated user-defined bridge and record the initial embedded DNS result.

## 4. Validation

- [x] 4.1 Run focused unit tests for HTTP client, proxy stream/WebSocket recovery, and deployment configuration.
- [x] 4.2 Run formatting, lint/type checks, full relevant test gates, and strict OpenSpec validation.

## 5. Live Network-Switching Revision

- [x] 5.1 Record the live failure showing Docker embedded DNS retained the old Wi-Fi forwarder and codex-lb poisoned account health.
- [x] 5.2 Add and test an opt-in Linux host-network launch that follows the host resolver, while documenting its isolation trade-off.
- [x] 5.3 Configure the running local container to use a stable systemd-resolved bridge listener without restarting codex-lb, then verify DNS.
- [x] 5.4 Re-run relevant gates and re-verify the corrected change before archive.
