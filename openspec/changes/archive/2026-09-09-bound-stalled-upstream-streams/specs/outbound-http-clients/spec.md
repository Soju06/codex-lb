## ADDED Requirements

### Requirement: Upstream streaming requests are bounded before the first response byte

A streaming upstream request MUST reach response headers within the effective stream
idle timeout. The bound applies from the moment the request is issued, so a connection
that is established but never answered fails on the same budget as a stream that stops
mid-flight.

Exceeding the bound MUST be reported with the existing `stream_idle_timeout` error code
and failure detail, MUST release every resource the attempt holds — including the
per-session response-create gate and any account lease — and MUST be eligible for the
same retry and failover handling as an idle timeout observed after the first byte.

Non-streaming control calls (token refresh, usage fetch, compaction) keep their own
timeouts and are unaffected.

#### Scenario: Established connection never returns response headers

- **GIVEN** an upstream connection that completes its TCP and TLS handshake
- **AND** the peer sends no response headers
- **WHEN** the effective stream idle timeout elapses
- **THEN** the attempt fails with `stream_idle_timeout`
- **AND** the failure is recorded before the request budget would have expired

#### Scenario: Response headers inside the bound stream normally

- **GIVEN** an upstream request whose response headers arrive before the idle timeout
- **WHEN** the stream then produces events with gaps shorter than the idle timeout
- **THEN** the request completes normally
- **AND** the pre-header bound does not truncate the stream

## MODIFIED Requirements

### Requirement: Upstream connectors persist across interactive turn gaps

The shared upstream TCP connectors MUST configure connection keepalive of at least 90 seconds and a DNS cache TTL of at least 300 seconds, so consecutive interactive requests reuse pooled connections and resolved names instead of re-handshaking per turn.

Because pooled connections outlive the requests that opened them, the connectors MUST
also enable OS-level TCP keepalive probes on upstream sockets, so a connection dropped
by an intermediary is reported as a transport error rather than waiting for an
application-level timeout. Probe tuning beyond enabling keepalive is best-effort:
platforms that do not expose the per-socket knobs MUST still enable keepalive and MUST
NOT fail client construction.

#### Scenario: Connector construction pins reuse settings

- **WHEN** the shared HTTP client initializes its direct TCP connectors
- **THEN** they are constructed with `keepalive_timeout >= 90` and `ttl_dns_cache >= 300`

#### Scenario: Pooled sockets carry keepalive probes

- **WHEN** the shared HTTP client creates an upstream socket
- **THEN** `SO_KEEPALIVE` is enabled on that socket
- **AND** client construction succeeds even when per-socket probe tuning is unavailable
