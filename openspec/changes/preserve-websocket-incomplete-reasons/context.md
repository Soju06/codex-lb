# Context

The upstream event already reaches the downstream client unchanged. This
change is deliberately limited to the proxy's terminal request-log settlement:
it does not retry an incomplete response, penalize the account, or expose a
new client-facing error format.

For example, an upstream terminal event with
`incomplete_details.reason = "max_output_tokens"` is logged with status
`error`, error code `max_output_tokens`, and error message
`max_output_tokens` instead of `upstream_error` and no message.
