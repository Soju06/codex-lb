# Compatibility Tooling

## Purpose

Define the reference materials and tooling used to validate OpenAI wire compatibility in this project.
## Requirements
### Requirement: Publish compatibility support matrix
The project MUST maintain a support matrix in `refs/openai-compat-test-plan.md` that lists supported and explicitly unsupported OpenAI-compatible features for Responses and Chat. The matrix MUST be updated whenever behavior changes.

#### Scenario: Support matrix present
- **WHEN** the compatibility plan is reviewed
- **THEN** the document includes a table of supported and unsupported features for Responses and Chat

### Requirement: Live compatibility check output
The live compatibility check script MUST print the expected unsupported feature list and MUST write a results JSON file to `refs/openai-compat-live-results.json`.

#### Scenario: Live check run
- **WHEN** `scripts/openai_compat_live_check.py` is executed
- **THEN** the console output includes an expected unsupported list and the JSON results file is written

### Requirement: Codex traffic parity tooling preserves transport identity

The compatibility toolkit MUST capture and identify Codex Responses traffic as
HTTP JSON, HTTP SSE, or WebSocket rather than collapsing all three into one
generic stream. It MUST support the public `/v1/responses` path and the native
`/backend-api/codex/responses` and `/codex/responses` path forms. HTTP capture
MUST retain one request/response pair per record; WebSocket capture MUST retain
frame direction and flow identity so multiple `response.create` lifecycles on
one connection can be reconstructed as separate turns.

#### Scenario: HTTP JSON response is classified independently from SSE

- **WHEN** a captured Responses POST returns a JSON content type and body
- **THEN** the record transport is `http_json`
- **AND** it is not reported as an SSE or WebSocket turn

#### Scenario: HTTP SSE response keeps its lifecycle

- **WHEN** a captured Responses POST returns `text/event-stream`
- **THEN** the record transport is `http_sse`
- **AND** the parser retains ordered event types, terminal event, usage, and the
  presence of the `[DONE]` sentinel when supplied

#### Scenario: Long-lived WebSocket carries multiple turns

- **GIVEN** one captured WebSocket flow contains two client
  `response.create` frames
- **WHEN** each create is followed by its upstream lifecycle events and a
  terminal event
- **THEN** the analyzer reconstructs two ordered WebSocket turns
- **AND** it does not merge their events into one turn

### Requirement: Three-path comparison distinguishes baseline from fidelity

The compatibility analyzer MUST accept optional Path A direct traffic plus
required Path B client-to-LB and Path C LB-to-upstream captures. It MUST treat B
and C as the same-run fidelity comparison and MUST report missing turns,
transport changes, request structure, ordered event lifecycle, terminal state,
usage, and tool differences. It MUST treat A as a structural direct baseline
and MUST NOT declare exact generated content, volatile ids, timing, or usage
from a separate A invocation to be a hard proxy mismatch. A strict CLI mode
MUST return a nonzero status when hard B/C mismatches exist.

#### Scenario: Same-run event loss fails strict comparison

- **GIVEN** Path C contains a `response.output_item.done` event in a turn
- **AND** the corresponding Path B turn omits it
- **WHEN** the analyzer runs in strict mode
- **THEN** the report identifies the ordered event lifecycle difference
- **AND** the process returns a nonzero status

#### Scenario: Direct baseline uses a different response id and token count

- **GIVEN** Path A is a separate direct invocation with the same request shape
- **AND** its generated response id and usage differ from Paths B and C
- **WHEN** the analyzer compares all three paths
- **THEN** those direct-run differences remain visible as baseline data
- **AND** they do not by themselves become a hard B/C mismatch

#### Scenario: Transport transition remains visible

- **GIVEN** the client-facing Path B is HTTP SSE
- **AND** the upstream Path C is WebSocket
- **WHEN** their common turn projections otherwise agree
- **THEN** the report identifies the B/C transport transition
- **AND** compares their Responses lifecycle fields without misclassifying the
  WebSocket frames as SSE

#### Scenario: Public Responses adapter rewrites remain comparable

- **GIVEN** Path B uses public `messages` input with instruction, multimodal,
  assistant, or tool-role items
- **AND** Path C contains the corresponding canonical Responses `instructions`
  and `input` items
- **WHEN** metadata capture compares the same-run request legs
- **THEN** known adapter rewrites and synthesized empty defaults do not fail
  strict comparison
- **AND** changed roles, item types, tool names, call identifiers, or continuity
  identifiers remain hard semantic mismatches

### Requirement: Traffic captures fail safe for credentials and body content

The capture addon MUST replace authorization, API-key, cookie, and proxy
credential header values in every capture mode. Metadata-only body capture MUST
be the default and MUST replace sensitive prompt, generated text, tool argument,
tool output, and encrypted-content strings with deterministic digest-and-length
metadata while retaining protocol structure. Raw body capture MUST require an
explicit full mode, and generated capture and report artifacts MUST be excluded
from version control.

#### Scenario: Default capture observes an authenticated request

- **WHEN** metadata mode captures a request with bearer credentials and prompt
  text
- **THEN** the credential value is absent from the record
- **AND** the raw prompt is absent
- **AND** stable digest-and-length metadata remains available for same-run
  equality comparison

#### Scenario: Operator explicitly requests full bodies

- **WHEN** the addon is configured for full body capture
- **THEN** request and response bodies are retained for deep investigation
- **AND** credential headers remain redacted

### Requirement: Server-observable parity keeps identity and transport dimensions separate

The traffic parity toolkit MUST compare direct Codex and codex-lb observations separately for HTTP protocol/ALPN, TLS handshake profile, HTTP identity headers, SSE framing, and WebSocket handshake/extension behavior. It MUST NOT report header parity as TLS or full traffic indistinguishability, and SHOULD identify dimensions that cannot be controlled without a shared egress origin.

#### Scenario: Header alignment does not conceal TLS mismatch

- **GIVEN** direct and proxied requests have matching normalized Codex identity headers
- **AND** their ALPN, cipher list, or TLS extension profile differs
- **WHEN** a parity report is generated
- **THEN** identity parity is reported independently
- **AND** TLS/HTTP transport remains a visible mismatch

### Requirement: Model discovery emits a Codex control identity

Subscription model-discovery requests MUST send the resolved Codex client version in the request query and a first-party Codex `User-Agent`, MUST send `originator` and `Accept: */*`, and MUST apply the same mapping for direct and account-routed egress. They MUST NOT expose the HTTP library's default User-Agent or add a standalone `version` header absent from the maintained direct-Codex profile.

#### Scenario: Routed and direct model discovery share identity

- **GIVEN** the same resolved Codex version, access token, and account
- **WHEN** model discovery runs once through direct egress and once through an account route
- **THEN** both requests carry the same Codex identity and accept headers
- **AND** neither carries an aiohttp-generated User-Agent or standalone
  `version` header

### Requirement: Upstream WebSocket handshake offers Codex-compatible compression

Direct and account-routed upstream WebSockets MUST use the Codex-pinned OpenAI `tokio-tungstenite` and `tungstenite` revisions when the fixed helper is available before dispatch and MUST enable their default `permessage-deflate` configuration. When the helper is unavailable before dispatch, account-routed and direct Python WebSocket clients MUST continue offering `permessage-deflate`. A route choice MUST NOT remove the extension family or change the preferred implementation family when the helper is present, and traffic reports MUST continue comparing negotiated extensions independently from TLS and header identity.

#### Scenario: Direct and routed WebSocket handshakes offer compression

- **WHEN** the proxy opens an upstream Responses WebSocket directly or through an account route
- **THEN** its handshake offers `permessage-deflate`
- **AND** the chosen route does not change the extension family

#### Scenario: Native direct handshake offers Codex compression

- **GIVEN** the fixed native helper is available
- **WHEN** a direct upstream WebSocket handshake is sent
- **THEN** it is serialized by the Codex-pinned tungstenite implementation
- **AND** it offers that implementation's default `permessage-deflate` parameters

#### Scenario: Routed handshake retains compression

- **WHEN** an account-routed upstream WebSocket handshake is sent
- **THEN** an available helper uses the same Codex-pinned native compression configuration as direct egress
- **AND** a pre-dispatch missing-helper fallback still offers `permessage-deflate`
- **AND** the traffic report does not collapse compression parity into TLS parity

### Requirement: Egress observer evidence is scoped and privacy safe

The traffic parity capture addon MUST accept an optional operator-supplied
observer id and observer role for A/C source-address comparison. It MUST retain
only a deterministic digest of the observer id and a keyed HMAC-SHA-256 of the
source host observed by the capture boundary, together with non-sensitive
address-family metadata, and MUST NOT persist the raw source host or HMAC key.
The same per-comparison key MUST cover both A/C captures and MUST be rotated
after the comparison. The analyzer MUST compare source evidence only when both
paths attest the same observer id and role and both contain a source-host HMAC
and address family. Missing, partial, or
incompatible attestation MUST be reported as unobserved rather than pass or
fail. An intercept-observer match MUST NOT be described as proof of the public
source IP or ASN observed by OpenAI; that stronger claim is available only
when an actual controlled origin is explicitly declared as the observer.

#### Scenario: Same intercept observer sees the same source

- **GIVEN** Path A and Path C attest the same intercept observer
- **AND** that observer records the same digested source host for both paths
- **WHEN** a server-observable report is generated
- **THEN** the observed-source dimension passes at the intercept boundary
- **AND** the report does not claim public OpenAI source-IP or ASN parity

#### Scenario: Capture has no common observer attestation

- **GIVEN** either path omits an observer id or the attested observers differ
- **WHEN** a server-observable report is generated
- **THEN** the observed-source dimension is reported as unobserved
- **AND** absence on both paths is not treated as equality

#### Scenario: Raw addresses remain absent

- **GIVEN** the capture boundary exposes a client peer address
- **WHEN** metadata, full, or none capture mode writes a record
- **THEN** the record contains a keyed source-host HMAC and address
  family
- **AND** the raw source host is absent

#### Scenario: Retained evidence resists offline source guessing

- **GIVEN** an attacker obtains the retained capture without the per-comparison
  HMAC key
- **WHEN** the attacker enumerates likely hostnames or private addresses
- **THEN** plain SHA-256 guesses do not reproduce the retained source evidence
- **AND** the key is absent from every retained artifact

### Requirement: Controlled captures can attest ASN from an offline database

The traffic capture addon MUST accept an optional operator-supplied ASN MMDB
path and MUST resolve the capture socket's peer address locally without making
a network lookup. When configured, it MUST record the ASN number, a digest of
the ASN organization, and database digest/build provenance, and MUST NOT retain
the raw source address or organization name. The analyzer MUST compare ASN
evidence only when A and C attest the same observer id and role and the same
database digest. Missing, failed, or incompatible evidence MUST be reported as
unobserved rather than pass. Only evidence captured with observer role `origin`
MAY be described as public egress-ASN evidence.

#### Scenario: Same controlled origin and database observe the same ASN

- **GIVEN** Path A and Path C use the same origin observer and ASN database
- **AND** both local lookups return the same ASN number and organization digest
- **WHEN** the server-observable report is generated
- **THEN** ASN parity passes with controlled-origin claim scope
- **AND** the record contains neither the raw source address nor organization
  name

#### Scenario: Database provenance differs

- **GIVEN** Path A and Path C contain ASN observations from different database
  digests
- **WHEN** the report compares ASN evidence
- **THEN** ASN parity is unobserved due to incompatible database provenance
- **AND** equal ASN numbers are not promoted to a pass

### Requirement: TLS extension-order parity is calibrated against direct traffic

The analyzer MUST accept an optional second direct-Codex capture as a TLS
randomization reference. For HTTP JSON, HTTP SSE, and WebSocket independently,
it MUST deduplicate records from the same ClientHello, require a configurable
minimum sample count in every compared cohort, and compare invariant TLS
capability fields exactly. When invariant profiles match, it MUST summarize
pairwise extension precedence and order entropy and MUST compare the A/C order
distance against a deterministic 95% bootstrap limit derived only from the two
direct cohorts. Raw JA3 and ClientHello hashes MUST remain informational and
MUST NOT be used as the extension-order parity gate. Missing samples MUST be
reported as unobserved rather than pass.

#### Scenario: Randomized orders remain within direct variance

- **GIVEN** two sufficiently sampled direct cohorts have the same stable TLS
  profile and randomized extension orders
- **AND** the codex-lb cohort has the same stable profile and an order distance
  within the direct-derived 95% limit
- **WHEN** TLS randomization parity is analyzed
- **THEN** the transport cohort passes
- **AND** differing raw JA3 hashes remain visible as informational evidence

#### Scenario: Load balancer emits a fixed or shifted order profile

- **GIVEN** the direct cohorts demonstrate randomized extension ordering
- **AND** the codex-lb cohort emits an order distribution beyond the
  direct-derived 95% limit
- **WHEN** TLS randomization parity is analyzed
- **THEN** that transport cohort fails
- **AND** stable-profile equality does not conceal the distribution mismatch

#### Scenario: A cohort has too few independent handshakes

- **GIVEN** any direct or codex-lb cohort has fewer than the configured minimum
  deduplicated ClientHello samples
- **WHEN** TLS randomization parity is analyzed
- **THEN** that transport cohort is reported as unobserved
- **AND** it is not reported as a pass

### Requirement: Controlled origin probe covers every Responses transport

The traffic parity toolkit MUST provide an explicitly launched deterministic
origin fixture that supports Codex model discovery and Responses HTTP JSON,
HTTP SSE, and WebSocket requests on the canonical public and Codex-native path
forms. The fixture MUST accept multiple ordered `response.create` turns on one
WebSocket connection, MUST return a terminal lifecycle for every accepted
turn, and MUST NOT perform upstream network calls or reflect request content or
credentials in responses. The fixture MUST accept Codex HTTP requests encoded
with zstd, decode them before JSON parsing, and enforce its request-size bound
independently on the encoded and decoded bodies. Malformed zstd and unsupported
content encodings MUST be rejected as client errors. HTTP request bodies and
WebSocket frames MUST be bounded. The fixture launcher MUST bind to loopback by
default and MUST reject a non-loopback bind unless the operator supplies an
explicit public-bind acknowledgement.

#### Scenario: HTTP transports terminate deterministically

- **WHEN** the fixture receives valid `stream=false` and `stream=true`
  Responses requests
- **THEN** it returns HTTP JSON and SSE respectively
- **AND** both responses end in `response.completed` without copying request
  content into the response

#### Scenario: One WebSocket carries multiple turns

- **GIVEN** a client opens one fixture WebSocket
- **WHEN** it sends two valid `response.create` messages
- **THEN** the fixture emits two separate ordered created/completed lifecycles
- **AND** keeps the connection open between turns

#### Scenario: Accidental public launch is rejected

- **WHEN** the fixture launcher is given a non-loopback host without explicit
  public-bind acknowledgement
- **THEN** it exits before opening a listening socket

#### Scenario: Real Codex zstd SSE request is accepted

- **GIVEN** a valid Responses JSON object compressed with zstd
- **WHEN** Codex sends it with `Content-Encoding: zstd`
- **THEN** the fixture decodes and parses the object
- **AND** returns the deterministic response in the requested HTTP mode

#### Scenario: Compressed request cannot bypass the fixture bound

- **GIVEN** a zstd request whose encoded body is within the request limit
- **AND** whose decoded body exceeds the request limit
- **WHEN** the fixture decodes the request
- **THEN** it rejects the request with payload-too-large status

#### Scenario: Invalid content encoding is rejected

- **WHEN** a request declares malformed zstd or an unsupported content encoding
- **THEN** the fixture rejects it as a client error
- **AND** does not interpret the encoded bytes as JSON

### Requirement: Controlled origin observations use the real socket boundary

The controlled-origin runbook MUST place a TLS/HTTP-capable reverse capture
boundary directly in front of the loopback fixture and configure the capture
addon with observer role `origin`. Path A and Path C MUST be captured in
separate files against the same observer id. Source equality from this setup
MAY be reported as controlled-origin public source evidence; forwarding headers
MUST NOT substitute for the capture boundary's socket peer. TLS certificate
trust, public listener exposure, and test-only credential handling MUST be
explicit operator responsibilities.

#### Scenario: Forwarded address cannot become origin evidence

- **GIVEN** a request supplies `Forwarded` or `X-Forwarded-For`
- **WHEN** the origin capture records source evidence
- **THEN** it derives the source from the capture process's client socket
- **AND** ignores the supplied forwarding headers for the source comparison

### Requirement: Controlled origin can inject bounded failure scenarios

The controlled traffic origin MUST keep success as its default and MUST accept
an operator-selected process scenario for HTTP 429, HTTP 503, delayed HTTP
response, incomplete SSE, WebSocket handshake rejection, or incomplete
WebSocket response. Scenario responses MUST NOT reflect request content or
credentials. Delay duration MUST be finite and validated. Request data MUST
NOT be able to select or alter the active scenario.

#### Scenario: Operator selects an HTTP rate limit

- **WHEN** the fixture starts with the HTTP 429 scenario
- **THEN** a Responses HTTP request receives status 429 and a deterministic
  `Retry-After` hint
- **AND** the response contains no request content or credential value

#### Scenario: SSE terminates before a terminal event

- **WHEN** the fixture starts with the incomplete SSE scenario
- **THEN** it emits `response.created` and ends without a terminal Responses
  event
- **AND** the analyzer marks the turn incomplete

#### Scenario: WebSocket fails before and after dispatch

- **WHEN** the fixture uses WebSocket rejection
- **THEN** it rejects the handshake before accepting a Responses turn
- **AND WHEN** it uses incomplete WebSocket response
- **THEN** it accepts one `response.create`, emits `response.created`, and
  closes without a terminal event

### Requirement: Traffic failure evidence is explicit and privacy safe

The capture addon MUST write a record for a targeted HTTP flow that ends in a
transport error before the ordinary response hook completes. It MUST retain
the request semantic projection, partial status/headers/body metadata when
available, and a bounded error category. It MUST NOT retain raw exception
text, peer addresses, proxy addresses, credentials, or a duplicate record for
an exchange already captured by the response hook.

The analyzer MUST report, for each B/C turn, HTTP status, normalized
`Retry-After`, terminal class, completeness, incomplete reason, and network
error category. It MUST classify success, HTTP rejection, terminal failure,
transport incompleteness, and network error separately. This failure evidence
MUST be informational and MUST NOT turn an incomplete or mismatched lifecycle
into a strict parity pass.

#### Scenario: Upstream timeout has no response envelope

- **GIVEN** a targeted Responses HTTP flow ends with a timeout before response
  headers
- **WHEN** the addon writes the capture
- **THEN** it records the timeout category and redacted request semantics
- **AND** it does not store the raw timeout message

#### Scenario: Both legs expose failure outcomes

- **GIVEN** Path B and Path C contain corresponding failed turns
- **WHEN** the analyzer compares them
- **THEN** the report shows each leg's status, terminal class, completeness,
  retry hint, and network-error category
- **AND** a failure-to-failure translation is visible independently from the
  strict semantic result

### Requirement: Failure analysis covers end-to-end and egress legs

When Path A, Path B, and Path C captures are supplied, the analyzer MUST report
failure outcomes for both A/B (direct Codex versus Codex through codex-lb) and
B/C (codex-lb egress versus controlled origin). Each comparison MUST expose
status, normalized `Retry-After`, terminal class, completeness, incomplete
reason, and bounded network-error category for both sides. A differing
`Retry-After` value on corresponding HTTP failure turns MUST fail strict
comparison rather than remain informational.

#### Scenario: A/B outcome translation is visible

- **GIVEN** direct Codex ends with a network error and routed Codex receives a
  synthetic terminal failure
- **WHEN** the analyzer compares Path A and Path B
- **THEN** the report shows the lifecycle translation in an A/B failure table

#### Scenario: Retry hint mismatch fails strict parity

- **GIVEN** corresponding failure responses have different normalized
  `Retry-After` values
- **WHEN** strict comparison runs
- **THEN** the result includes a retry-hint mismatch and does not pass

### Requirement: Captures preserve privacy-safe HTTP header sequence evidence

The traffic capture addon MUST record request header field names in observed
serialization order, including duplicate occurrences and original casing, and
MUST NOT add header values to this sequence evidence. The analyzer MUST compare
normalized field-name order and exact casing independently for Path A and Path
C. Missing sequence evidence MUST be reported as unobserved rather than pass.
The comparison MUST remain informational and MUST NOT claim HPACK or HTTP/2
frame parity from decoded headers.

#### Scenario: Header order and casing match

- **GIVEN** Path A and Path C contain ordered request header-name evidence
- **WHEN** normalized order and exact casing are equal
- **THEN** both header sequence dimensions match

#### Scenario: Casing differs without an order change

- **GIVEN** Path A records `content-type` and Path C records `Content-Type` in
  the same position
- **WHEN** the report compares header sequence evidence
- **THEN** normalized order matches
- **AND** exact casing does not match

#### Scenario: Evidence is absent

- **GIVEN** either capture predates header sequence metadata
- **WHEN** the report compares the paths
- **THEN** both header sequence dimensions are unobserved
- **AND** missing evidence is not promoted to a match

### Requirement: Controlled origin captures privacy-safe raw HTTP/2 profiles

The traffic toolkit MUST provide an explicitly launched TLS HTTP/2 controlled
origin that records the client connection preface, ordered initial SETTINGS,
bounded connection-control and request frame metadata, stream identifiers,
connection reuse, decoded header-name order/casing, and header-block fragment
lengths/digests. It MUST NOT retain decoded header values, HPACK bytes, DATA
bytes, request bodies, socket peer addresses, TLS key material, or certificate
private keys. The origin MUST bind to loopback by default and MUST require an
explicit acknowledgement for a non-loopback bind.

#### Scenario: Multiple requests reuse one observed connection

- **GIVEN** a client negotiates `h2` and sends multiple controlled requests on
  one TLS connection
- **WHEN** the observer writes request records
- **THEN** the records share a connection identifier and use distinct stream
  identifiers
- **AND** no header value or request body is retained

#### Scenario: Non-h2 or oversized input is rejected safely

- **GIVEN** a client does not negotiate `h2` or exceeds a configured bound
- **WHEN** it connects to the controlled observer
- **THEN** the connection or stream is closed without an unbounded allocation
- **AND** rejected payload bytes are not persisted

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

### Requirement: Raw HTTP/2 parity gates wire-profile changes

Changes to native HTTP/2 startup settings or native Codex header serialization
MUST be verified with controlled A′, A, and C captures. A′ and A MUST establish
direct repeatability, and A/C ordered SETTINGS, connection-control shape,
decoded header-name order/casing, and stream/reuse pattern MUST all match before
the wire-profile change is considered verified. HPACK fragments MUST remain
informational.

#### Scenario: Native wire-profile fix is verified

- **GIVEN** focused unit tests pass and independent A′/A direct profiles match
- **WHEN** the fixed native helper is captured as Path C
- **THEN** every stable A/C HTTP/2 profile dimension matches
- **AND** credential values, request bodies, HPACK bytes, and TLS keys are not
  retained as evidence

### Requirement: Composite traffic parity gate fails closed

The traffic toolkit MUST provide a single offline gate that combines same-run
B/C semantic comparison, independently sampled A′/A/C TLS comparison,
controlled A′/A/C raw HTTP/2 comparison, and an optionally required controlled
failure matrix. It MUST require explicit non-zero coverage for every configured
semantic and TLS transport and every configured failure scenario. Strict
success MUST require zero semantic hard mismatches, matching required TLS
cohorts, every stable direct-repeatability and routed HTTP/2 dimension to be
observed and matching, and every required failure scenario to satisfy its
end-to-end policy. Missing, malformed, undersampled, or incomplete required
evidence MUST fail the gate.

#### Scenario: Complete evidence passes

- **GIVEN** required SSE and WebSocket semantic turns match B/C
- **AND** required HTTP JSON, SSE, and WebSocket TLS cohorts match
- **AND** all A′/A and A/C raw HTTP/2 dimensions match
- **AND** every required controlled failure scenario satisfies its A/B policy
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

#### Scenario: Required failure scenario is absent

- **GIVEN** the composite policy requires HTTP 429 behavior
- **AND** no HTTP 429 comparison result is supplied
- **WHEN** the composite gate runs
- **THEN** the failure section and aggregate result fail closed

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

### Requirement: Controlled failure matrix gates client-visible recovery

The traffic toolkit MUST project each configured controlled scenario onto
bounded attempt counts, A/B outcome classes, HTTP statuses, retry hints,
completion state, per-attempt relations, and final relation. A scenario MUST
require equal non-zero A/B attempt counts, compatible A/B turns, a compatible
final outcome, and its versioned expected profile. Scenarios whose contract is
successful or transparent HTTP rejection MUST also require the ordinary strict
B/C semantic gate. Expected incomplete transport scenarios MAY retain a strict
B/C mismatch when their explicit A/B recovery profile matches; the report MUST
show that distinction rather than call the raw transport identical.

#### Scenario: Transparent HTTP rejection passes

- **GIVEN** direct and through-LB Codex each make one HTTP 429 attempt
- **AND** both observe status 429 with the same bounded retry hint
- **AND** B/C strict semantics pass
- **WHEN** the failure matrix is evaluated
- **THEN** the HTTP 429 scenario passes

#### Scenario: WebSocket recovery matches end to end

- **GIVEN** direct and through-LB Codex make the expected equal attempt count
- **AND** their attempt relations and final successful outcome match the
  versioned WebSocket recovery profile
- **WHEN** an upstream framing translation remains visible on B/C
- **THEN** the scenario passes its client-visible gate
- **AND** the report does not relabel B/C framing as exact

#### Scenario: Attempt count drifts

- **GIVEN** direct Codex makes three attempts and through-LB Codex makes two
- **WHEN** the failure matrix is evaluated
- **THEN** the scenario fails even if both final outcomes are successful

### Requirement: Version-aware traffic canary runs without false success

The canary runner MUST execute its configured fast live suite when the detected
Codex version differs from the last successful version or the last successful
run is at least the configured weekly interval old. It MUST serialize runs
with an exclusive lock, invoke an argv without a shell, use a new approved
scratch run directory, and atomically advance state only after exit 0. The
configured argv MUST delegate suite orchestration, gate evaluation, cleanup,
privacy scanning, and result generation to a repository-owned testable module;
host-local configuration MUST supply explicit paths rather than embed a second
suite implementation. Missing configuration, overlap, timeout, command
failure, incomplete cleanup, or failed privacy checks MUST NOT advance the
successful version or timestamp.

#### Scenario: Codex version changes

- **GIVEN** the last successful state records Codex 0.150.1
- **AND** the installed client reports 0.151.0
- **WHEN** the daily checker runs
- **THEN** it launches the fast live suite with trigger `version_changed`
- **AND** records 0.151.0 only if the suite succeeds

#### Scenario: Weekly interval elapses

- **GIVEN** the Codex version is unchanged
- **AND** the configured interval has elapsed since the last success
- **WHEN** the checker runs
- **THEN** it launches the suite with trigger `interval_elapsed`

#### Scenario: Another canary owns the lock

- **WHEN** a scheduled checker overlaps an active canary
- **THEN** the new checker exits without starting a second suite
- **AND** it does not alter successful state

#### Scenario: Host configuration invokes repository orchestration

- **GIVEN** the host scheduler decides a canary is due
- **WHEN** it invokes the configured command
- **THEN** the command uses explicit repository, runner, auth, and approved
  scratch paths
- **AND** repository-owned code performs validation, cleanup, scanning, and
  result generation

#### Scenario: Suite command fails after creating sensitive state

- **GIVEN** a controlled runner created an isolated database, key, or log
- **WHEN** a later suite step fails
- **THEN** enumerated sensitive subtrees are removed before the command exits
- **AND** no successful result or scheduler state is written

#### Scenario: Fast canary succeeds

- **WHEN** raw HTTP/2 and controlled failure gates pass and cleanup completes
- **THEN** the run is labelled `fast_canary`
- **AND** it is not reported as a full TLS/composite attestation
