# Rust migration architecture

Normative owner: [Proxy architecture OpenSpec](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/proxy-architecture/spec.md).

codex-lb uses one virtual Cargo workspace at the repository root. Rust source
lives under `crates/`; it is not nested under `rust/` or `native/`. This is the
intended final layout, not a temporary helper layout: when the Python backend is
fully retired, the root workspace and existing crate paths remain in place and
only new application/domain crates and binaries are added.

## Current boundaries

```text
Cargo.toml                         virtual workspace and shared policy
Cargo.lock                        one reproducible application lockfile
rust-toolchain.toml               pinned compiler, rustfmt, and clippy
deny.toml                         advisory, license, and source policy
crates/
  codex-lb-protocol/              versioned Python/Rust IPC data contract
  codex-lb-responses/             synchronous Responses semantics and collection
  codex-lb-egress/                reusable HTTP, TLS, and WebSocket transport
  codex-lb-egress-worker/         stdio process lifecycle and binary target
app/core/clients/native_egress.py Python adapter and replay-safe ownership
```

The dependency direction is one way: the worker depends on egress, and egress
depends on protocol and Responses. The protocol and Responses crates have no
async runtime or networking dependencies. Application policy must not move into the worker merely because
the worker is Rust: Python currently retains account selection, endpoint
ordering, retries, health classification, and persistence.

The worker binary deliberately contains only startup and exit behavior. This
keeps the egress implementation usable from a future in-process Rust server;
the transport will not need to be extracted from a subprocess executable when
that migration reaches the application shell.

Direct usage GETs also prefer the existing helper after the direct-egress
authorization check, unless a Python retry client was explicitly supplied.
Python retains usage validation and retries; Rust owns each HTTP exchange.
Retryable status responses close before backoff without waiting for their body.
Final body failures retain transport errors, while plain-text error bodies keep
their message. Only an unavailable helper on the first attempt permits Python
fallback. Routed usage remains owned by `CodexClient`; credit consumption is a
separate call. See the [outbound client contract](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/outbound-http-clients/spec.md).

Direct and account-routed streaming and compact Responses requests delegate SSE byte framing to egress.
The adapter requires `http_sse_v1` and supplies the existing idle timeout and
event byte limit as per-request options. Rust owns the deadline between body
reads, including partial events. Ordinary HTTP streaming additionally requires
`http_responses_events_v1`: the synchronous Responses library normalizes legacy
text/audio/audio-transcript aliases and classifies event types. Unchanged event
text stays byte-for-byte intact. With `http_responses_completion_v1`, Rust stops
at recognized `response.completed`, `response.failed`, and `response.incomplete`
events, flushes their fragments, and releases the body without waiting for EOF.
The final fragment carries `stream_complete`; Python validates it and retires
the exchange without a cancellation round trip. Python retains completion for
context-dependent error handoffs, archives, and public error mapping. HTTP error bodies and
non-streaming requests keep the raw chunk contract.

Interpreted events carry the effective type and an explicit Python-normalization
marker on the final fragment. Error conversion and JSON representations that
cannot be rewritten exactly (such as alias payloads containing floats, huge
integers, or escaped surrogate strings) use that marker without replaying the
request. Type metadata is limited to 16 KiB too; longer types use the same
handoff. Shared Python/Rust fixtures pin SDK and native passthrough behavior.
WebSocket Responses classification uses `websocket_responses_events_v1` and
embeds the raw payload in IPC for the Python decoder. Persistent socket lifetime,
request matching, sequence tracking, retries, and settlement remain in Python;
a terminal response does not close a shared WebSocket.

Compact requests additionally require `http_compact_sse_v1`. Their
`content_type_aware` framing option preserves raw JSON success bodies, while
the exact `text/event-stream` media type (ignoring parameters and case) and
absent/empty Content-Type use native framing. Both Python compact paths apply
the same comparison, so non-SSE types or parameters mentioning event-stream
retain JSON parsing. Compact SSE additionally requires `http_compact_collect_v1`:
Rust collects output items and assembles the completed response in the reusable
Responses library. Python receives one fragmented result, retains public shape
normalization and error translation, and returns as soon as `response.completed`
arrives without waiting for HTTP EOF. Unknown fields and large integers remain
opaque JSON; indexed items use last-write precedence and numeric ordering,
followed by unindexed done items. The Python collector serves only the
missing-helper transport. A null total timeout stays unset; explicit total, connection, and SSE idle
deadlines retain their meaning. The adapter and bundled helper must be updated
together because an older helper cannot honor this contract.

Routed requests carry typed `native_sse` options through `CodexClient` with
`buffer_response=False`. Python selects each concrete endpoint and records
fallback metadata, while preserving the returned native response for framed
consumption. A confirmed pre-dispatch connect failure may use the next endpoint;
body errors and cancellation cannot replay a dispatched POST. If the helper is
unavailable before dispatch, the resolved Python transport uses ordinary byte
framing, and receives no native-only option. A locally created routed client
finishes closing its session even in an already cancelled scope. Compact
responses likewise retain their transport and owned routed session through
consumption, and finish cleanup on completion, failure, or cancellation,
including cancellation before the native response headers arrive.

SSE text crosses IPC in fragments of at most 16 KiB of UTF-8, with an explicit
continuation flag. This keeps individual JSON lines and the existing bounded
queue small even for large events or escaped control characters. The adapter
joins text fragments without scanning or decoding the SSE bytes again.
Compact result envelopes use the same text-fragment bound. Intermediate compact
events remain inside Rust, including output-item updates; Python only decodes
the resulting response, terminal error, or invalid-completion envelope.
The framing contract and cancellation behavior are specified by
[outbound HTTP clients](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/outbound-http-clients/spec.md) and
[Responses compatibility](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/responses-api-compat/spec.md).

## Compatibility contract

Every new helper process completes `client_hello` / `server_hello` negotiation
before receiving a request. The adapter requires protocol version 1 and all
capabilities needed by the current Python call sites. An installed but
incompatible helper fails closed before dispatch; it is not treated as a
missing helper and no ambiguous request is replayed through Python.

`crates/codex-lb-protocol/tests/fixtures/handshake-v1.json` is the shared
cross-language handshake fixture. Wire changes must follow these rules:

1. Add backward-compatible optional fields when possible.
2. Add a capability when behavior, acknowledgement, or failure semantics
   change without requiring a new wire grammar.
3. Increment the protocol version for an incompatible grammar or meaning.
4. Keep old-version fixtures while an independently deployable adapter or
   worker may still use them.
5. Test malformed, missing-capability, cancellation, and process-exit paths;
   success-only compatibility tests are insufficient.

## Adding a migrated slice

Prefer a vertical slice with an explicit boundary over a generic utilities
crate. Domain rules belong in a domain crate, reusable infrastructure in a
focused adapter crate, and executable wiring in an application crate. Shared
types should be promoted only after two real consumers need them.

For each slice:

- Define ownership and retry/cancellation semantics before implementation.
- Keep credential-bearing values out of errors, traces, and IPC diagnostics.
- Expose a narrow library API; do not make other crates depend on a binary.
- Add cross-language contract fixtures while Python remains a consumer.
- Cut over one owner at a time and retain a rollback path only at a proven
  pre-dispatch boundary.
- Remove the Python implementation and IPC surface after the Rust owner is
  proven; do not preserve a permanent dual implementation.

Likely future top-level crates are `codex-lb-domain`, `codex-lb-application`,
focused infrastructure adapters, and a server binary. Those names should be
introduced when their boundaries exist rather than scaffolded empty today.

## Tooling and dependency policy

Run the same checks as CI with:

```bash
make rust-check
```

This checks formatting, Clippy with warnings denied, all workspace tests, and
the release worker build using the committed lockfile. `make rust-audit`
additionally requires `cargo-deny`; CI always runs the pinned cargo-deny action.

Workspace dependencies are declared once in the root manifest. Wire-sensitive
Codex transport dependencies stay exactly pinned, including the audited OpenAI
WebSocket fork revisions. Ordinary dependencies may use compatible ranges, but
every production build uses `Cargo.lock` and `--locked`. New licenses and Git
sources require an explicit `deny.toml` policy change and review.

Unsafe Rust is forbidden workspace-wide. Exceptions, if ever necessary, need
a narrowly scoped crate-level policy, a documented invariant, and targeted
tests rather than weakening the workspace default.
