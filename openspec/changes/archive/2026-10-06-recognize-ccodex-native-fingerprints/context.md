# Context

Issue #2560 reports stock Codex app-server traffic relayed by CCodex using the `ccodex-internal` (0.5.x) and `ccodex-handoff-worker` (0.4.x) identities. Those requests carry User-Agent values such as `ccodex-internal/0.159.3 (...)`. If neither identity is recognized, the proxy replaces the upstream User-Agent, originator, and version with its non-native Codex CLI fallback fingerprint.

The existing `_is_native_codex_request` helper is shared by HTTP and WebSocket upstream header construction. The change therefore extends its fingerprint-specific originator and User-Agent allowlists, and tests both header builders. The originator allowlist used for transport selection remains unchanged; recognizing CCodex for fingerprint preservation does not opt it into WebSocket routing. Matching remains allowlist-based: a name such as `ccodex-unlisted/1.0` stays non-native and follows the established normalization path.

Example accepted fingerprints:

- `originator: ccodex-internal`
- `User-Agent: ccodex-handoff-worker/0.4.2 (Ubuntu; x86_64)`

This change covers the first request in issue #2560 only. Shipping #2174 and introducing an upstream error code are separate requests and are not included.
