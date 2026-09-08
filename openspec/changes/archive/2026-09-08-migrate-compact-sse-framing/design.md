# Design

Compact keeps its Python request/response policy but shares the native SSE
framer used by ordinary Responses. A per-request `content_type_aware` option
selects framing for successful responses whose Content-Type contains
`text/event-stream` case-insensitively, or is absent/empty, matching the existing
compact parser. Other success bodies and all HTTP errors remain raw bytes.
The adapter derives the same mode from the request options and response headers;
real-worker tests cover this cross-language decision. Ordinary SSE options retain
their unconditional-success framing semantics.

`http_compact_sse_v1` covers that option and `timeout_ms: null`, which means no
transport total deadline. Positive connection and SSE idle limits still apply.
An old helper must reject negotiation before a POST can be sent. No new public
setting is introduced. Explicit compact deadlines remain finite across both
header and body consumption, including a stream with continuous partial bytes.

Routed compact must request `buffer_response=False`, preserve a native response,
and close it before closing an owned routed client. Direct compact uses native
egress inside the existing account circuit-breaker context. Both use Python
only when the helper is absent before dispatch. A transport/protocol/body failure
after dispatch never triggers Python fallback or another endpoint attempt.

For example, a response containing output_item.done and response.completed can
return the normalized compact payload immediately while upstream keeps HTTP
open. The owned request is cancelled/closed at that boundary; another request
on the same native helper remains usable. JSON success still uses the existing
compact normalizer and does not pass through an SSE byte/text decoder.
