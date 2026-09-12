## ADDED Requirements

### Requirement: Python WSS connections reuse system verification context

The Python `websockets` fallback for upstream `wss://` connections MUST reuse a system-default SSL verification context across separate connections and outbound-client refreshes within one initialized application lifecycle. The context MUST preserve `ssl.create_default_context()` system/environment trust, certificate validation and hostname checking, and MUST NOT add the aiohttp client's certifi roots. Initialization MUST make the context available before ordinary request handshakes, and full outbound-client close followed by initialization MUST rebuild it from the then-current trust inputs. Plain `ws://` connections MUST receive no server-TLS context. Existing proxy resolution, native/routed transport selection and handshake cancellation cleanup MUST remain unchanged.

#### Scenario: Separate secure connections reuse the verification context
- **WHEN** multiple Python WSS connections open during one lifecycle, including after shared HTTP-client refresh
- **THEN** they use the same system verification context without rebuilding its trust store per handshake
- **AND** close followed by reinitialization uses a newly constructed system context

#### Scenario: Trust and hostname validation remain effective
- **WHEN** the Python WSS fallback connects to a server trusted by the configured system/default roots with a matching hostname
- **THEN** the TLS handshake succeeds
- **AND** an untrusted chain or mismatched hostname is rejected without disabling verification or adding certifi roots

#### Scenario: Plain WebSocket and cancellation behavior are preserved
- **WHEN** the Python fallback opens a `ws://` connection or its handshake is cancelled
- **THEN** a plain connection is not given an SSL context
- **AND** cancellation preserves the existing transport cleanup and propagation behavior
