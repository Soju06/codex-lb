## ADDED Requirements

### Requirement: Responses Lite signaling enforces all-turns reasoning context

Every final upstream Responses payload that codex-lb advertises as Responses
Lite—by the canonical HTTP header or the canonical per-request websocket
client-metadata marker, whether body-derived, bridge-preserved, or
continuity-trusted—MUST contain the exact JSON string
`reasoning.context = "all_turns"`. Before upstream serialization, the service
MUST create a reasoning object when it is omitted or null and MUST replace an
absent, null, blank, differently-cased, otherwise different-string, or
non-string context value with `"all_turns"`. It MUST preserve
`reasoning.effort`, `reasoning.summary`, and every unrelated reasoning member.

This normalization MUST be idempotent, MUST NOT establish Lite classification
or continuity trust, MUST NOT reject an otherwise-valid Lite request solely for
a context mismatch, and MUST NOT remove the Lite signal. For requests not
advertised as Lite, this normalization MUST leave the client-supplied reasoning
shape unchanged. An invalid non-object reasoning container remains subject to
the existing client-payload validation contract.

#### Scenario: Body-derived Lite HTTP request omits reasoning

- **WHEN** a normalized HTTP Responses body contains an `additional_tools` input item and omits or nulls `reasoning`
- **THEN** the final upstream HTTP body contains `reasoning.context = "all_turns"`
- **AND** the request carries the canonical Responses Lite HTTP header

#### Scenario: Existing Lite reasoning members survive normalization

- **WHEN** a Responses Lite body includes reasoning effort, summary, or extension members and its context is absent, null, blank, differently cased, another string, or a non-string value
- **THEN** the final upstream body contains the exact string `reasoning.context = "all_turns"`
- **AND** every unrelated reasoning member retains its client-supplied value

#### Scenario: Compact Lite request uses the same invariant

- **WHEN** a compact request is advertised upstream as Responses Lite
- **THEN** its final upstream POST body contains `reasoning.context = "all_turns"`
- **AND** it carries the canonical Responses Lite HTTP header

#### Scenario: Websocket and HTTP fallback agree on Lite reasoning

- **WHEN** a body-derived Lite request is prepared for upstream websocket transport
- **THEN** its `response.create` body contains both the canonical Lite client-metadata marker and `reasoning.context = "all_turns"`
- **BUT WHEN** the websocket handshake falls back to upstream HTTP
- **THEN** the HTTP body retains `reasoning.context = "all_turns"`, the marker is absent, and the canonical Lite HTTP header is present

#### Scenario: HTTP bridge transformations preserve the invariant

- **GIVEN** an HTTP bridge request established Lite mode from an `additional_tools` prefix
- **WHEN** bridge trimming or retry builds a final `response.create` body whose input delta no longer contains that prefix
- **THEN** the body retains the internally derived canonical Lite marker
- **AND** it contains `reasoning.context = "all_turns"`

#### Scenario: Trusted marker-only continuation is normalized

- **GIVEN** a same-model websocket continuation has trusted Lite continuity to its referenced previous response
- **WHEN** its incremental body carries the canonical marker but omits the original `additional_tools` prefix
- **THEN** the final upstream body contains `reasoning.context = "all_turns"`
- **AND** the canonical marker remains present

#### Scenario: Untrusted and non-Lite requests are not normalized

- **WHEN** a non-Lite request supplies arbitrary reasoning context, an inbound Lite header, or a stale or otherwise untrusted websocket marker
- **THEN** the existing signal rules omit or strip the untrusted Lite signal
- **AND** this normalization does not alter the request's client-supplied reasoning shape
