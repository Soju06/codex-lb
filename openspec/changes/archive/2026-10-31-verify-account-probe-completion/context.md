The default remains a limiter wakeup; old callers and test doubles need no new
argument. The frontend can explicitly send `{"mode":"completion","model":"gpt-5.5"}`
and read `model`, `outcome`, `latencyMs`, `errorCode`, `errorMessage` and the existing
snapshot fields. `probeStatusCode` describes the upstream HTTP response, including
200 for an unsuccessful SSE terminal. Zero means no HTTP response was observed.

The stream has a fixed total deadline and event size limit. It uses a fixed dot
prompt, no reasoning/text overrides, and never selects another account. Requests
use the normal `stream_responses` HTTP path, including `to_payload()` serialization,
native egress, standard fingerprint/identity encoding, and owned generator cleanup.
The core client gains optional event/error-read caps and HTTP status observation;
omitting these caps preserves existing client behavior.
`max_output_tokens` is unsupported by that upstream and is omitted; actual bounds
are the tiny fixed prompt, total/idle deadlines, event limit and stream limit.
Terminal errors preserve safe code identifiers and sanitized messages (at most 512 characters).
The existing log sanitizer masks keyed secrets and Bearer credentials; probe-local
redaction also removes exact access-token material, bare JWTs, API keys and URLs.
Only parsed error fields are eligible; response output, headers and exception text
are never returned. Non-success HTTP bodies are read incrementally up to 64 KiB,
and only structured errors are exposed. For example, HTTP 200 followed by
`response.failed` with `token_expired` reports status 200, outcome `failed`, and
errorCode `token_expired`. This change stays active for parent integration.
