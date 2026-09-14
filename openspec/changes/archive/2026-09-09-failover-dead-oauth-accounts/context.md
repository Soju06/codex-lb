# Revoked-token status compatibility

The Images adapter drains the existing Responses stream after account routing
and failover. Its collector defaults a missing upstream error type to
`server_error`, and the HTTP route uses an Images-specific code-to-status map.
The shared Responses status helper alone therefore cannot preserve HTTP 401 on
this surface.

For example, a terminal `response.failed` event whose error is
`{"code":"token_revoked","message":"Token was revoked"}` must retain HTTP 401
on both image generation and edit routes, including the Codex aliases. The
Images code override takes precedence over the collector's default type; no
new retry policy, account ownership behavior, or error-envelope rewriting is
needed.
