# Responses traffic parity analysis

Use the traffic parity toolkit to compare a direct Codex request with both
edges of the same request through codex-lb. The analyzer keeps HTTP JSON, HTTP
SSE, and WebSocket as distinct transports while projecting them into a common
Responses turn model.

The normative tooling contract is
[`openspec/specs/compatibility-tooling`](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/compatibility-tooling).
The native cutover is governed by
[`outbound-http-clients`](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/outbound-http-clients),
[`responses-api-compat`](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/responses-api-compat),
and
[`deployment-installation`](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation).

## What the three paths mean

| Path | Traffic | Comparison role |
|---|---|---|
| A′ | Codex client → controlled origin directly | Optional direct-only TLS randomization reference |
| A | Codex client → OpenAI/ChatGPT directly | Optional structural baseline from a separate invocation |
| B | Codex client → codex-lb | Client-visible side of the same proxied invocation |
| C | codex-lb → OpenAI/ChatGPT | Upstream side of the Path B invocation |

Paths B and C are the fidelity oracle. Path A is useful context, but generated
text, response ids, token counts, and timing from a separate model invocation
are not expected to match exactly.

The output calls out the transport on every turn. A B-side SSE stream and a
C-side WebSocket can therefore be compared semantically without pretending
that their framing is identical.

## Safety and storage

The addon always redacts authorization, API-key, cookie, and proxy credential
headers. Its default `metadata` body mode replaces prompt text, generated text,
tool arguments/output, and encrypted content with deterministic SHA-256 and
byte-length metadata. This preserves same-run equality checks without storing
the raw value.

Codex session/thread identifiers and `x-codex-turn-metadata` are also hashed in
metadata mode because those headers can contain workspace and repository
details even when they contain no credential.

For request fields that codex-lb legitimately rewrites, the addon also records
an adapter-aware semantic projection before discarding raw text. This lets
metadata mode compare joined instructions, normalized multimodal parts, and
assistant/tool message conversions while still treating role, item type, tool
name, and call/continuity identifiers as material.

!!! warning

    A capture still contains request structure, sizes, protocol enums, and
    equality fingerprints for values such as models and tool names. Treat it
    as sensitive diagnostic data. Use
    `capture_body_mode=full` only when the metadata capture cannot explain a
    difference.

Raw captures can grow quickly. Keep them outside the repository, for example:

```bash
RUN_DIR=/mnt/scratch/bench/codex-traffic-parity/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$RUN_DIR"
umask 077
openssl rand 32 > "$RUN_DIR/source-observer.key"
```

`/mnt/scratch` is not backed up; move only a sanitized, curated result that is
worth retaining to the appropriate durable storage tier.

## Start a capture

The addon is an optional mitmproxy tool and is not part of the codex-lb runtime
dependencies:

```bash
uvx --from mitmproxy mitmdump \
  -s scripts/traffic_analysis/mitmproxy_addon.py \
  --set capture_output="$RUN_DIR/path_c.jsonl" \
  --set capture_body_mode=metadata \
  --set capture_observer_id="$RUN_DIR" \
  --set capture_observer_role=intercept \
  --set capture_source_hmac_key_file="$RUN_DIR/source-observer.key" \
  -p 18081
```

Use the same `capture_observer_id` for Path A and Path C only when those
captures represent the same observation boundary. Use the same HMAC key for the
A/C pair, never retain it with the evidence, and rotate it after the comparison.
The observer id is stored only as a SHA-256 digest. The default `intercept` role
proves which HMAC-digested source host
the capture proxy saw; it does not prove which public source IP OpenAI saw.
Use `capture_observer_role=origin` only when this addon is running at an actual
controlled origin boundary. The raw source host and source port are never
written.

### Controlled origin source probe

The repository includes a deterministic test origin for proving the public
source address seen at a socket boundary. It supports model discovery,
Responses HTTP JSON/SSE, and multiple Responses turns on one WebSocket. It
does not call OpenAI, validate credentials, or copy request content into its
responses.

Run the fixture on loopback at the controlled origin host:

```bash
uv run python -m scripts.traffic_analysis.origin_fixture \
  --host 127.0.0.1 \
  --port 19090
```

The launcher rejects a non-loopback bind unless `--allow-public-bind` is
explicitly supplied. The recommended topology keeps the fixture private and
exposes only a TLS reverse-capture process:

```text
Codex or codex-lb ── TLS/HTTP/WS ──> public reverse capture ── HTTP ──> 127.0.0.1:19090
```

On the controlled origin, use a real DNS name and a certificate PEM containing
the certificate chain and private key. Bind the public listener only after its
host firewall allows the intended test sources. To attest ASN, obtain a
MaxMind-compatible ASN MMDB through the database provider's normal licensed
process and place it outside the repository. The addon never downloads or
updates a database:

```bash
PROBE_HOST=probe.example
PROBE_CERT=/secure/probe.example.pem
OBSERVER_ID=codex-origin-probe-2026-08-28
SOURCE_HMAC_KEY=/secure/codex-source-observer.key
ASN_DB=/secure/GeoLite2-ASN.mmdb

uvx --with maxminddb --from mitmproxy mitmdump \
  --mode reverse:http://127.0.0.1:19090 \
  --listen-host 0.0.0.0 \
  --listen-port 443 \
  --certs "$PROBE_HOST=$PROBE_CERT" \
  -s scripts/traffic_analysis/mitmproxy_addon.py \
  --set capture_output="$RUN_DIR/path_a-origin.jsonl" \
  --set capture_body_mode=metadata \
  --set capture_observer_id="$OBSERVER_ID" \
  --set capture_observer_role=origin \
  --set capture_source_hmac_key_file="$SOURCE_HMAC_KEY" \
  --set capture_asn_mmdb="$ASN_DB"
```

Collect Path A, stop the public listener, restart it with
`capture_output="$RUN_DIR/path_c-origin.jsonl"`, and collect Path C using the
same hostname and observer id. Point direct Codex at
`https://probe.example/v1`; point an isolated codex-lb instance at
`CODEX_LB_UPSTREAM_BASE_URL=https://probe.example/backend-api`. Capture Path B
normally while invoking the isolated instance.

For Path A, a provider with a disposable token avoids sending the user's
OpenAI login to the probe:

```toml
[model_providers.origin-probe]
name = "OpenAI"
base_url = "https://probe.example/v1"
wire_api = "responses"
env_key = "ORIGIN_PROBE_TOKEN"
supports_websockets = true
requires_openai_auth = false
```

Run both direct lanes because Codex selects them from provider capability:

```bash
ORIGIN_PROBE_TOKEN=non-secret-probe-token codex exec --ephemeral \
  -c 'model_provider="origin-probe"' \
  -m gpt-5.6-luna \
  'Return exactly ORIGIN_PROBE_OK.'

# Repeat with supports_websockets=false for the HTTP/SSE lane.
```

Codex 0.150.1 zstd-compresses its HTTP/SSE request body. The fixture decodes
that request before JSON parsing and applies its 1 MiB bound separately to the
encoded and decoded bodies. Malformed zstd and other content encodings are
rejected as client errors.

Use a disposable direct-provider token rather than a real OpenAI key wherever
the Codex provider configuration permits it. codex-lb still sends its selected
account authorization to its configured upstream, so use an isolated
short-lived test account and a controlled host you trust. Metadata capture
redacts the authorization value, but the TLS endpoint necessarily receives
it. Remove the public listener and private key from the probe host after the
run.

The origin addon derives source evidence from the accepted client socket. It
does not trust `Forwarded`, `X-Forwarded-For`, or similar request headers. A
matching `origin` observation therefore proves equal public source addresses
at this controlled boundary. When `capture_asn_mmdb` is configured, the record
also contains the numeric ASN, a SHA-256 digest of its organization, and the
database digest/build metadata. It never contains the raw source address or
organization name. ASN parity is comparable only when A and C use the same
observer and exact database digest; it does not guarantee that a different
destination uses the same policy route.

### Controlled failure-path matrix

The fixture defaults to the normal success path. To compare failure behavior,
restart it with exactly one operator-selected scenario; requests cannot select
or override the scenario:

```bash
python scripts/traffic_analysis/origin_fixture.py \
  --failure-scenario http_429 \
  --failure-delay-seconds 30
```

Use the same scenario for the direct Codex path and the codex-lb path:

| Scenario | Transport | Controlled outcome |
| --- | --- | --- |
| `http_429` | HTTP/SSE | JSON `429` with a bounded `Retry-After` hint |
| `http_503` | HTTP/SSE | JSON `503` |
| `http_timeout` | HTTP/SSE | Operator-configured bounded response delay |
| `sse_incomplete` | SSE | `response.created`, then EOF without a terminal event |
| `websocket_reject` | WebSocket | Reject before accepting the turn |
| `websocket_incomplete` | WebSocket | `response.created`, then close with code `1011` |

The comparison output records the same HTTP status, normalized retry hint,
terminal class, completeness, incomplete reason, and bounded network-error
category in two views. `failure_path_a_vs_b` shows direct Codex versus the
client-visible codex-lb outcome, including attempt counts and the final outcome;
`failure_path_b_vs_c` shows the same-run LB ingress/egress translation. Both
views label exact matches, failure translations, and success/failure mismatches.
They remain explanatory: incomplete or malformed turns still fail the strict
gate, and a differing B/C `Retry-After` value is a hard mismatch.

After collecting all seven scenarios (including `success`) beneath one root,
gate the bounded end-to-end profile separately from raw B/C framing:

```bash
uv run python -m scripts.traffic_analysis.failure_matrix \
  --root "$FAILURE_RUN" \
  --output "$FAILURE_RUN/failure-matrix.md" \
  --json-output "$FAILURE_RUN/failure-matrix.json" \
  --strict
```

The baseline contains attempt counts, statuses, retry hints, outcome classes,
and relations only. Timeout and incomplete scenarios may have an expected raw
B/C strict failure while still passing the A/B client-visible recovery gate.

On first use mitmproxy creates a local CA under `~/.mitmproxy`. Only install or
trust that CA in the test process or disposable test environment.

### Path C: codex-lb to upstream

Run the Path C proxy on port `18081`, then start codex-lb with its upstream
proxy variables pointing at it:

```bash
https_proxy=http://127.0.0.1:18081 \
wss_proxy=http://127.0.0.1:18081 \
SSL_CERT_FILE="$HOME/.mitmproxy/mitmproxy-ca-cert.pem" \
CODEX_LB_UPSTREAM_STREAM_TRANSPORT=auto \
uv run fastapi run app/main.py --port 2455
```

`auto` exercises the normal transport decision. Set the transport to `http` or
`websocket` for a controlled lane, then run both lanes when investigating a
transport-specific regression. If the account has an explicit upstream proxy
route, route it through the capture proxy as well; explicit account routing can
otherwise bypass environment proxies.

### Path B: client to codex-lb

Use mitmproxy reverse mode so localhost proxy bypass rules cannot skip the
capture:

```bash
uvx --from mitmproxy mitmdump \
  --mode reverse:http://127.0.0.1:2455 \
  -s scripts/traffic_analysis/mitmproxy_addon.py \
  --set capture_output="$RUN_DIR/path_b.jsonl" \
  --set capture_body_mode=metadata \
  -p 18082
```

Point the test Codex provider at the reverse proxy instead of codex-lb
directly:

```toml
[model_providers.codex-lb-capture]
name = "openai"
base_url = "http://127.0.0.1:18082/backend-api/codex"
wire_api = "responses"
env_key = "CODEX_LB_API_KEY"
supports_websockets = true
requires_openai_auth = true
```

Run one deterministic scenario. Reusing the same prompt, tool declarations,
reasoning effort, and service tier makes request transformations easier to
classify. Paths B and C are collected simultaneously from this run.

### Path A: optional direct baseline

Start another regular capture on port `18080` with output `path_a.jsonl`, then
run the same scenario with the ordinary direct Codex provider and the test
process's `https_proxy`/`wss_proxy` set to that port. This is a separate model
invocation; use it to establish protocol shape, not byte-for-byte output
identity.

### Path A′: TLS randomization reference

For a statistically meaningful ClientHello comparison, make a second direct
capture in `path_a_reference.jsonl`. Collect 50–100 independent TLS
connections for each transport under test in A′, A, and C. Repeated HTTP/2
requests or WebSocket frames on one connection count as one sample because the
analyzer deduplicates `client_hello_sha256`. Use fresh client/helper processes
or a controlled origin that closes each connection so every sample performs a
new handshake.

Keep HTTP JSON, HTTP SSE, and WebSocket runs separate. The analyzer reports
each transport independently and returns `N/A`, never PASS, when any cohort has
fewer than 20 independent ClientHellos. `--tls-min-samples` may raise this
floor for a stricter experiment; lowering it is intended only for fixture
smoke tests.

## Analyze captures

Generate a machine-readable comparison:

```bash
uv run python -m scripts.traffic_analysis.compare \
  --path-a-reference "$RUN_DIR/path_a_reference.jsonl" \
  --path-a "$RUN_DIR/path_a.jsonl" \
  --path-b "$RUN_DIR/path_b.jsonl" \
  --path-c "$RUN_DIR/path_c.jsonl" \
  --json-output "$RUN_DIR/comparison.json"
```

Add `--strict` when a hard B/C mismatch should return a nonzero exit status.
Transport changes are reported but do not fail solely because the two edges
use different framing.

Generate the Markdown investigation report:

```bash
uv run python -m scripts.traffic_analysis.generate_report \
  --path-a-reference "$RUN_DIR/path_a_reference.jsonl" \
  --path-a "$RUN_DIR/path_a.jsonl" \
  --path-b "$RUN_DIR/path_b.jsonl" \
  --path-c "$RUN_DIR/path_c.jsonl" \
  --output "$RUN_DIR/report.md"
```

Review at least:

- per-turn B/C transport and any transition;
- end-to-end A/B failure attempts and final outcome;
- B/C `Retry-After` preservation on HTTP rejections;
- missing or malformed turns and orphan WebSocket frames;
- model, reasoning, service-tier, tool, and continuity-field transformations;
- ordered material Responses lifecycle events and terminal class;
- function-call identity/correlation and terminal usage details;
- raw differences classified as expected transport wrappers versus hard
  semantic mismatches.

### Composite regression gate

After collecting the semantic, repeated TLS, and raw HTTP/2 evidence families,
combine them into one fail-closed verdict:

```bash
uv run python -m scripts.traffic_analysis.composite_gate \
  --semantic-path-a "$SEMANTIC_RUN/path_a.jsonl" \
  --semantic-path-b "$SEMANTIC_RUN/path_b.jsonl" \
  --semantic-path-c "$SEMANTIC_RUN/path_c.jsonl" \
  --tls-path-a-reference "$TLS_RUN/path_a_reference.jsonl" \
  --tls-path-a "$TLS_RUN/path_a.jsonl" \
  --tls-path-c "$TLS_RUN/path_c.jsonl" \
  --h2-path-a-reference "$H2_RUN/path_a_reference.jsonl" \
  --h2-path-a "$H2_RUN/path_a.jsonl" \
  --h2-path-c "$H2_RUN/path_c.jsonl" \
  --failure-root "$FAILURE_RUN" \
  --require-failure-matrix \
  --output "$RUN_DIR/composite-gate.md" \
  --json-output "$RUN_DIR/composite-gate.json" \
  --strict
```

The defaults require B/C HTTP SSE and WebSocket semantic coverage, all three
HTTP JSON/SSE/WebSocket TLS cohorts with at least 20 independent ClientHellos,
and every stable A′/A plus A/C raw HTTP/2 dimension. Use repeated
`--require-semantic-transport` or `--require-tls-transport` flags only when an
experiment has an explicitly narrower contract. A required but absent,
malformed, or undersampled lane fails instead of becoming `N/A` or an implicit
pass.

The aggregate outputs contain compact verdicts and SHA-256/byte-count
attestations for the source files. They do not copy capture bodies, header
values, HPACK fragments, WebSocket payloads, or per-sample ClientHello hashes.
HTTP duration and WebSocket flow-span percentiles are diagnostic only and do
not participate in strict parity because the compared processes and model
invocations have independent scheduling.

### Version and weekly fast canary

This host installs a user timer that checks the installed Codex version daily.
It runs the controlled raw HTTP/2 suite and all seven failure scenarios when
the version changes or seven days have elapsed since the last successful run:

```bash
systemctl --user status codex-traffic-parity-canary.timer
journalctl --user -u codex-traffic-parity-canary.service -n 100

# Check whether work is due without changing state.
uv run python -m scripts.traffic_analysis.canary_runner \
  --config /home/ubuntu/work/codex-lb/traffic-parity-canary/config.json \
  --dry-run

# Operator-forced run through the same lock and success contract.
uv run python -m scripts.traffic_analysis.canary_runner \
  --config /home/ubuntu/work/codex-lb/traffic-parity-canary/config.json \
  --force
```

The timer unit is `codex-traffic-parity-canary.timer`; its host-local config,
state, and lock live under
`/home/ubuntu/work/codex-lb/traffic-parity-canary/`. The config invokes the
repository-owned `scripts.traffic_analysis.fast_canary_suite` module with
explicit repo, runner, auth, and approved scratch paths; it contains no suite
logic. Each run receives a new directory under
`/mnt/scratch/bench/codex-traffic-parity/`. State advances only after the gates
pass, database/key/log cleanup completes, and the retained tree passes the
credential-shape privacy scan. Overlap, command timeout, a missing scenario,
or invalid result leaves the previous success unchanged.

The scheduled result is labelled `fast_canary`. It does not include the
independently sampled HTTP JSON/SSE/WebSocket ClientHello cohorts and therefore
must not be reported as a full composite or TLS attestation. Run the composite
gate with at least 20 ClientHellos per TLS cohort for a release, monthly check,
or TLS-stack change.

### Server-observable A↔C profile

When Path A is present, the report also keeps these dimensions independent:

- HTTP version and negotiated ALPN;
- negotiated TLS version/cipher and ClientHello metadata, including offered
  cipher suites, extensions, groups, key shares, signature algorithms, and the
  JA3 digest;
- Codex identity headers;
- decoded request header-name order, duplicate occurrences, and original
  casing, without additional header values;
- SSE framing observations;
- WebSocket handshake and extension negotiation.
- source-host equality at a common attested capture observer, when configured.
- ASN number/organization equality under the same offline database provenance,
  when configured.

This section is informational and deliberately does not turn matching headers
into a claim of full traffic equivalence. Missing observer attestation is shown
as `N/A`, not a match. An `intercept` observer match covers only that capture
boundary; public source IP and ASN evidence require a controlled `origin`
observation, and ASN additionally requires the same offline database digest.

When A′ is supplied, the report also compares repeated TLS samples per
transport. Cipher suites, extension sets and lengths, groups, key shares,
signature algorithms, ALPN, and negotiated TLS fields must match exactly after
normalizing standard GREASE values and excluding the order-constrained PSK
extension. Extension serialization order is summarized as pairwise precedence
probabilities and mean binary entropy. A↔C passes the order check only when its
mean pairwise distance is within the larger of the observed A′↔A distance and
a deterministic direct-only bootstrap 95% limit. Raw JA3 and ClientHello
hashes remain informational.

### Raw HTTP/2 SETTINGS and header-block profile

Use the separate controlled HTTP/2 origin when decoded HTTP captures are not
enough. It observes client bytes after TLS termination and before hyper-h2
decoding. The JSONL records contain only the connection preface result, frame
type/flags/stream id/payload length, ordered initial SETTINGS, WINDOW_UPDATE
increments, stream reuse, decoded header names, and opaque SHA-256/length
metadata for HEADERS and CONTINUATION payloads. Header values, HPACK bytes,
DATA bytes, request bodies, source addresses, and TLS secrets are never stored.

Create or provision a short-lived certificate outside the repository. For a
loopback-only smoke run, a disposable self-signed certificate is sufficient:

```bash
mkdir -p "$RUN_DIR/tls"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj '/CN=localhost' \
  -keyout "$RUN_DIR/tls/observer.key" \
  -out "$RUN_DIR/tls/observer.crt"

uv run python -m scripts.traffic_analysis.h2_observer \
  --cert "$RUN_DIR/tls/observer.crt" \
  --key "$RUN_DIR/tls/observer.key" \
  --output "$RUN_DIR/path_a-h2.jsonl"
```

The listener defaults to `127.0.0.1:19443`, accepts only ALPN `h2`, and rejects
a non-loopback bind unless `--allow-public-bind` is present. For a remote
controlled origin, use a trusted DNS certificate, restrict the host firewall
to the intended test sources, and delete the short-lived private key after the
run. The tool does not generate or persist certificate material itself.

Point the direct Codex provider at this origin and collect the HTTP lane. Stop
the listener, restart it with `--output "$RUN_DIR/path_c-h2.jsonl"`, and point
the isolated codex-lb upstream at the same origin. Use a disposable probe token
because a TLS endpoint necessarily receives credentials even though its
capture record omits their values. An optional second direct run supplies A′:

```bash
uv run python -m scripts.traffic_analysis.http2_profile \
  --path-a-reference "$RUN_DIR/path_a-reference-h2.jsonl" \
  --path-a "$RUN_DIR/path_a-h2.jsonl" \
  --path-c "$RUN_DIR/path_c-h2.jsonl" \
  --output "$RUN_DIR/http2-profile.md" \
  --json-output "$RUN_DIR/http2-profile.json" \
  --strict
```

The strict A↔C gates are ordered initial SETTINGS, pre-request connection
control shape, decoded header-name order/casing, normalized request DATA
segmentation, stream ids, and connection reuse. A′↔A is direct-client variance
context for the standalone report and a required repeatability gate in the
composite report. Missing evidence is `N/A` in the standalone report and a
failure when the composite gate requires it.
HPACK fragment lengths and digests are informational only: equality does not
prove equal decoded values or dynamic-table history, and a difference does not
by itself prove a semantic mismatch.

DATA segmentation compares `max` versus `partial` frame classes and ordered
END_STREAM/PADDED flags using the advertised maximum frame size. It deliberately
does not compare DATA bytes or the variable partial-tail length, so independent
turn body sizes do not manufacture a mismatch while a different chunking policy
still does.

The connection-control projection excludes client SETTINGS ACK frames. An ACK
reacts to the controlled origin's server SETTINGS and can cross the first
HEADERS boundary as server scheduling changes; it is not a client-selected
startup profile. Initial non-ACK SETTINGS and WINDOW_UPDATE frames remain exact
gates.

## Capture modes

| Mode | Body handling | Intended use |
|---|---|---|
| `metadata` | Sensitive strings become digest + byte length | Default parity investigation |
| `full` | Raw JSON/SSE/WebSocket bodies retained; headers still redacted | Short-lived deep debugging |
| `none` | Body/frame payloads omitted | Header, status, route, and timing checks |

`none` captures are useful for manual transport diagnostics, but a B/C strict
comparison fails with `insufficient_capture_body` because it cannot prove
request, event payload, usage, or tool-call fidelity.

The capture addon deliberately buffers an HTTP SSE response until mitmproxy's
response hook. It compares event semantics, not TCP read sizes or HTTP chunk
boundaries. Its TLS ClientHello hook records a credential-free fingerprint and
hash, but never stores the raw handshake. Extension order can be randomized by
the client, so use the A′/A/C repeated-sample analysis instead of treating one
JA3 value as a stable identity. The controlled raw HTTP/2 origin covers
SETTINGS, frame shape, and opaque header-block fragments. Packet/raw-socket
tooling is still required for exact TLS record boundaries, TCP behavior, and
physical chunk timing.

Header sequence evidence describes the decoded field names presented by the
HTTP stack. It can reveal order, duplicate, or casing differences, but it does
not expose header values beyond the separately redacted header map and cannot
prove HPACK representation, dynamic-table state, or HTTP/2 SETTINGS/frame
parity.

## Native Codex egress

Official Linux containers include the locked `codex-lb-native-egress` helper.
When present, direct and account-routed model discovery, Responses HTTP/SSE,
and Responses or Live WebSockets use the pinned Codex-family Rust stack.
WebSockets use the OpenAI Codex 0.150.1 tungstenite fork revisions, default
`permessage-deflate`, native TLS roots, and transport-owned ping/pong liveness.
Missing-helper detection is safe and zero-configuration; source and wheel
installations retain the Python transports. A Responses POST, WebSocket
handshake, or frame is never replayed through Python after an ambiguous native
failure.

For account routes, Python still owns account selection, ordered endpoint
fallback, route metadata, and health classification. It passes one concrete
HTTP, HTTPS, SOCKS5, or SOCKS5H endpoint to the helper for each attempt. Only a
confirmed pre-dispatch connection failure can gain the existing safe endpoint
fallback; TLS verification and ambiguous delivery remain non-replayable.

Each codex-lb worker owns one persistent helper. Compatible direct and routed
HTTP requests share reqwest pools partitioned by effective proxy and connect
timeout, and WebSockets are independently multiplexed in that process. Workers
do not share pools with each other. The native HTTP pool uses the maintained
Codex initial HTTP/2 SETTINGS and connection-window profile, and native
Responses/model-discovery header names retain the maintained direct order.
Each helper generation negotiates a versioned capability contract before the
first request. A present but incompatible binary fails before dispatch instead
of silently falling back to Python.
Source IP/ASN (which follows the selected proxy), HPACK history, TCP behavior,
TLS extension order, and request/frame timing remain separately observable
dimensions. Decoded header order and casing are captured and compared, but
their compressed wire representation remains informational.
