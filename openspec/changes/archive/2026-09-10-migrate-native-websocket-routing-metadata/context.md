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

The adapter and bundled helper must be updated together for
`websocket_responses_routing_v1`; an incompatible helper fails closed before
dispatch. No new deployment mechanism is introduced.

Interpreted payloads admit integer tokens up to 640 digits, excluding the sign.
This is Python's smallest configurable integer-string limit, so it protects even
processes configured below the default 4,300 digits. Larger integers anywhere
in an object (including nested or overwritten values) keep the entire frame
opaque. The legacy Python parser may reject that exchange, but its integer
conversion cannot fail the shared IPC reader or interrupt peer exchanges.
For example, a 5,000-digit sequence is relayed as original text, while a
640-digit negative sequence remains interpreted without precision loss.
Strings and floating-point tokens do not use Python's integer conversion limit.
