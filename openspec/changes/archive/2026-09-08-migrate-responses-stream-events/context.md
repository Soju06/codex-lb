# Context

The existing synchronous Responses library owns ordinary HTTP event alias
normalization and classification. The transport opts in through a capability;
Python still owns SDK error conversion because it uses request IDs, timestamps,
and public error policy. The same library boundary can serve WebSocket later.

Canonical event-line classification deliberately retains the Python fast path,
including mismatched or malformed canonical JSON. Other frames use joined SSE
data fields. Struct deserialization is gated to JSON objects because serde also
accepts arrays. Alias reserialization preserves insertion order, compact JSON,
and Python ASCII escaping. Floats, out-of-range integers, escaped surrogates,
and ambiguous duplicate classification fields use an explicit Python handoff.
Unchanged mixed line endings remain untouched. For example, ordinary
`response.text.delta` becomes `response.output_text.delta` in Rust, while an
alias payload containing `1e-7` uses Python's existing serialization.

The helper emits bounded text fragments and final-fragment metadata. A type
larger than 16 KiB uses a handoff without metadata; metadata consumes queue bytes.
Malformed metadata fails the request without replay. Existing original-byte
limits, body-read idle deadlines, terminal handling and cancellation still apply.

This slice reduces Python ownership; it does not establish a throughput gain.
Synthetic measurements show remaining IPC and scheduling costs; see verification.
No operator setting or new application policy is introduced.
