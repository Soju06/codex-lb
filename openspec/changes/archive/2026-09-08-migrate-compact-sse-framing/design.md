# Design

Compact keeps its Python request/response policy but shares the native SSE
framer used by ordinary Responses. A per-request `content_type_aware` option
selects framing for successful responses whose Content-Type media type equals
`text/event-stream` ignoring parameters and case, or is absent/empty. Both Python
compact paths use the same comparison; substring matches in non-SSE media types
or parameters no longer select framing. Other success bodies and all HTTP errors remain raw bytes.
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

HTTP execution returns its terminal event to the runtime. The runtime writes
that event after leaving the cancellation select, because stdout flush can
yield after the event is already visible to the parent process. Prioritizing
the execution future alone does not prevent a cancellation from interrupting
that flush and emitting a second terminal during stdin EOF shutdown.

Compact retains its existing dedicated read budget: when an effective compact
timeout is set, it also supplies the SSE idle timeout; otherwise the ordinary
stream idle timeout applies. The transport still enforces the total deadline
independently across the entire request. Imposing the ordinary stream idle
limit on compact with an explicit budget would change the pre-migration behavior.
