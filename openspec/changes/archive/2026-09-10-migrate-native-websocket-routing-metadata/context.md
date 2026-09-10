# Ownership and compatibility

Rust owns payload-only response-ID extraction and integer sequence recognition.
Python retains Pydantic lifecycle validation. A valid lifecycle response's
nonempty nested ID wins without stripping; otherwise Rust's payload ID wins.
For example, a valid completed event with nested ID ` nested ` and top-level ID
`direct` matches ` nested `. If response.status is invalid, validation fails
and the same event instead matches `direct`. Porting Pydantic's entire lifecycle
model solely to reproduce that precedence would duplicate policy.

Payload ID extraction uses Python string.strip semantics, including U+001C
through U+001F, and the last duplicate JSON key. Selected lone-surrogate IDs
retain opaque delivery. Sequence values exclude booleans, floats and exponent
notation but preserve negative and arbitrary-precision integers. Raw JSON
integer tokens cross IPC without Rust numeric conversion.

This does not transfer pending-queue ownership. Sequence watermark updates stay
after successful downstream delivery. Suppressed replay-created events, send
failures, retry and settlement retain their current policy. Bridge multiline
SSE interpretation and non-Responses/oversized/unsupported opaque frames retain
their existing Python path. No throughput improvement is claimed.
