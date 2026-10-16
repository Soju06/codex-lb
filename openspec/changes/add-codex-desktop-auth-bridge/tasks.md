## 1. Contract

- [x] 1.1 Record the proposal, normative `desktop-auth-bridge` spec, and change context.

## 2. Implementation

- [x] 2.1 Read private credential files with bounded, no-follow, owner/mode-checked access (`credentials.py`).
- [x] 2.2 Encode target, Host/Origin, bearer, and header-rewrite policy as pure functions (`policy.py`).
- [x] 2.3 Relay HTTP opaquely with bounded bodies, no redirects, and fail-fast concurrency (`relay.py`).
- [x] 2.4 Relay WebSocket handshakes and messages with no redirects, bounded lifetime, and owned pumps (`relay.py`).
- [x] 2.5 Wire the opt-in `codex-lb desktop-bridge` CLI subcommand to `run_bridge`.
- [x] 2.6 Publish `docs/desktop-bridge.md`, linked from client setup and back to the OpenSpec capability.
- [x] 2.7 Bound process shutdown with active HTTP and WebSocket relays; cancel and await owned relay work, close connections, release slots, and verify these outcomes with integration tests.

## 3. Verification and delivery

- [x] 3.1 Unit-test credential parsing, file permission checks, target/Host/Origin policy, and header rewriting.
- [x] 3.2 Integration-test HTTP and WebSocket relays against a loopback stub upstream, including 3xx/401 masking, size limits, partial-failure closes, close-code relay, and slot release.
- [x] 3.3 Run `ruff`, `ty`, and the focused test suites.
- [x] 3.4 Validate the change with `openspec validate --strict`.
- [x] 3.5 Complete an isolated real-client acceptance run: model discovery, normal WebSocket response, prediction-mode fork/turn.
- [x] 3.6 Manually verify the visible Composer UI on macOS: suggestion appeared in the same thread after about 10 seconds and Tab accepted it. This confirms the tested setup; realtime/mobile remain outside this initial scope.
- [x] 3.7 Sync the capability into `openspec/specs/desktop-auth-bridge/`.
- [x] 3.8 Submit for maintainer review after local acceptance ([PR #2597](https://github.com/Soju06/codex-lb/pull/2597)).
- [x] 3.9 Verify the strict docs build, abrupt WebSocket disconnect, and recovery after an incomplete credential write following independent review.
- [ ] 3.10 Archive the verified change after merge with `--skip-specs` because the canonical spec is already synchronized.
