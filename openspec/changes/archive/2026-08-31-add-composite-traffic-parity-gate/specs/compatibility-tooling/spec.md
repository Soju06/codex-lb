## MODIFIED Requirements

### Requirement: HTTP/2 profile comparison separates gates from observations

The toolkit MUST compare Path A and Path C ordered initial SETTINGS,
client-initiated pre-request connection-control shape, decoded header-name
sequence, stream-id pattern, connection reuse, and body-size-independent DATA
segmentation independently. It MUST accept optional Path A′ evidence for
direct-client variance. A SETTINGS ACK generated in reaction to observer server
settings MUST be excluded from the stable connection-control projection because
its position relative to first HEADERS is server-timing dependent. Missing
evidence MUST be unobserved rather than pass. HPACK fragment digests and sizes
MUST remain informational and MUST NOT be treated as proof of decoded-value or
dynamic-table equality.

#### Scenario: Stable HTTP/2 profiles match

- **GIVEN** A and C contain complete controlled records with equal initial
  SETTINGS, client-initiated connection-control shape, header-name sequence,
  stream/reuse pattern, and normalized DATA segmentation
- **WHEN** the HTTP/2 profile report is generated
- **THEN** each observed stable dimension matches
- **AND** HPACK fragment evidence remains informational

#### Scenario: SETTINGS differ

- **GIVEN** A and C advertise different ordered initial SETTINGS
- **WHEN** the HTTP/2 profile report is generated
- **THEN** SETTINGS parity fails independently of header-name parity

#### Scenario: Observer response timing moves SETTINGS ACK

- **GIVEN** A′ sends its SETTINGS ACK before first HEADERS and A sends the same
  ACK after first HEADERS
- **WHEN** their stable connection-control shapes are compared
- **THEN** the reactive ACK position does not create a direct-variance mismatch
- **AND** non-ACK SETTINGS and WINDOW_UPDATE frames remain exact evidence

#### Scenario: Request sizes differ but segmentation policy matches

- **GIVEN** A and C send different request body sizes
- **AND** both use ordered maximum-size DATA frames followed by one partial
  END_STREAM frame
- **WHEN** their normalized DATA segmentation is compared
- **THEN** the segmentation dimension matches
- **AND** neither DATA bytes nor the variable tail length are retained in that
  projection

#### Scenario: DATA chunking policy differs

- **GIVEN** A uses maximum-size frames and C uses smaller intermediate frames
- **WHEN** their normalized DATA segmentation is compared
- **THEN** the segmentation dimension fails independently of header parity

## ADDED Requirements

### Requirement: Composite traffic parity gate fails closed

The traffic toolkit MUST provide a single offline gate that combines same-run
B/C semantic comparison, independently sampled A′/A/C TLS comparison, and
controlled A′/A/C raw HTTP/2 comparison. It MUST require explicit non-zero
coverage for every configured semantic and TLS transport. Strict success MUST
require zero semantic hard mismatches, matching required TLS cohorts, and every
stable direct-repeatability and routed HTTP/2 dimension to be observed and
matching. Missing, malformed, undersampled, or incomplete required evidence
MUST fail the gate.

#### Scenario: Complete evidence passes

- **GIVEN** required SSE and WebSocket semantic turns match B/C
- **AND** required HTTP JSON, SSE, and WebSocket TLS cohorts match
- **AND** all A′/A and A/C raw HTTP/2 dimensions match
- **WHEN** the composite gate runs in strict mode
- **THEN** it exits successfully and reports every section as passed

#### Scenario: A transport is absent

- **GIVEN** semantic comparison has no WebSocket turn on one required leg
- **WHEN** the composite gate runs
- **THEN** semantic coverage fails even if all observed turns match

#### Scenario: Direct HTTP/2 repeatability fails

- **GIVEN** A/C match but a stable A′/A HTTP/2 dimension differs
- **WHEN** the composite gate runs
- **THEN** the aggregate result fails rather than accepting an unstable direct
  baseline

### Requirement: Composite evidence remains compact and privacy safe

The composite gate MUST identify every input by path label, byte count, and
SHA-256 digest without copying capture payloads into its output. It MAY report
bounded HTTP-duration and WebSocket-flow-span distributions, but timing MUST
remain informational until an explicit repeated-sample statistical policy is
specified. It MUST NOT report timing equality as wire indistinguishability.

#### Scenario: Aggregate evidence is emitted

- **WHEN** the composite gate writes JSON and Markdown
- **THEN** both outputs contain section verdicts and input digests
- **AND** they omit authorization values, request bodies, WebSocket payloads,
  HPACK bytes, TLS keys, and per-sample ClientHello hashes

#### Scenario: Timing distributions differ

- **GIVEN** A and C have different latency summaries
- **WHEN** all required parity sections otherwise pass
- **THEN** timing remains visible as informational evidence
- **AND** it does not change the strict verdict
