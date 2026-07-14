## MODIFIED Requirements

### Requirement: Stuck HTTP bridge response-create gate sessions are retired

When a visible HTTP bridge request times out waiting for a per-session
response-create gate, the proxy MUST retire the bridge session only if a
pending visible request still owns the gate, is still awaiting
`response.created`, has not produced downstream-visible output, and its age
meets or exceeds the configured stuck-gate retirement threshold. Receiving a
non-visible upstream event before `response.created`, including
`codex.rate_limits`, MUST NOT by itself suppress retirement because such an
event neither assigns the response nor releases the gate. The retirement MUST
emit a structured low-cardinality log and a Prometheus counter without raw keys
or prompt content. Pre-created `response.*` lifecycle activity MUST count as
response progress and suppress stuck-gate retirement even when it has not yet
produced downstream-visible text.

#### Scenario: Leading rate-limit telemetry does not mask a stuck pre-created request

- **GIVEN** a visible HTTP bridge request owns the response-create gate
- **AND** upstream emits `codex.rate_limits` but never emits `response.created`
- **AND** the pending request becomes older than the configured stuck-gate retirement threshold
- **WHEN** another visible request times out waiting for that gate
- **THEN** the proxy retires the stuck bridge session
- **AND** the waiter is rejected cleanly with `response_create_gate_timeout`

#### Scenario: Healthy active stream is not retired during a normal wait

- **GIVEN** a pending HTTP bridge request has received `response.created` or produced downstream-visible output
- **WHEN** another visible request times out waiting for the gate
- **THEN** the proxy does not classify the active stream as a stuck pre-created gate owner

#### Scenario: Pre-created response lifecycle activity is not retired

- **GIVEN** a pending HTTP bridge request has not received `response.created`
- **BUT** upstream is emitting `response.*` lifecycle events for that request
- **WHEN** another visible request times out waiting for the gate
- **THEN** the proxy does not retire the actively progressing request
