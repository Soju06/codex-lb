# Standalone search v1 alias

## Evidence and purpose

On 2026-09-07, logs from all three production HA backends showed local
`Method Not Allowed` errors for `POST /v1/alpha/search`. Examples occurred at
02:26:51 UTC and 02:36:43 UTC. An unauthenticated probe reproduced HTTP 405 on
that path while `/backend-api/codex/alpha/search` correctly reached authentication
and returned HTTP 401. These failures occur before upstream account selection.

## Decision and constraints

Attach the v1 route to the existing handler so control-request authentication,
account affinity, failover, required-capability restrictions, body fidelity, and
response filtering remain shared. The existing doubled-prefix middleware also
supports `/backend-api/codex/v1/alpha/search`. Trailing-slash requests retain
the existing HTTP 405 behavior on both base URLs. No new environment variable
or client configuration is needed.

## Example and failure modes

A client with base URL `https://proxy.example/v1` sends a standalone search to
`POST /v1/alpha/search`. It now follows the same forwarding path as
`POST /backend-api/codex/alpha/search`, targeting upstream `/codex/alpha/search`.
Missing or invalid proxy credentials still fail authentication; final upstream
errors retain their HTTP status and the existing OpenAI error envelope.

## Specification validation

OpenSpec CLI 1.12.0 validates this change and the modified
`responses-api-compat` capability strictly. The delta requirement exactly matches
the synced main requirement.

Repository-wide validation reports a pre-existing missing Purpose section in
`model-source-routing` (58 of 59 specs pass standard validation). Strict mode
also rejects placeholder Purpose sections in 22 other specs (36 of 59 pass).
All 23 affected spec files were compared byte-for-byte with `HEAD` and are
unchanged by this fix. Full reports are local artifacts at
`/tmp/codex-lb-search-spec-validation.json` and
`/tmp/codex-lb-search-spec-validation-standard.json`.
