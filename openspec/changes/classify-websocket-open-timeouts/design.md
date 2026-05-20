## Context

The proxy already maps upstream WebSocket open timeouts to a client-facing
`upstream_unavailable` OpenAI error. Operators need a narrower persisted
classification because a timeout during opening handshake has different
debugging implications than a generic upstream error after a request is already
in flight.

The same live investigation path relies on service-tier diagnostics and
request-log snapshots. Those diagnostics should help correlate runtime shape
without exposing prompt content, request payloads, tokens, or auth material.

## Decisions

- Preserve the existing client-facing error code and message for compatibility.
- Attach failure-phase metadata at the WebSocket client boundary so proxy
  settlement can classify only opening-handshake timeouts.
- Translate `websocket_open_timeout` plus `upstream_unavailable` into the
  persisted request-log code `upstream_websocket_open_timeout`.
- Keep service-tier mismatch logs content-safe, limited to request id, response
  id, kind, model, transport, status, and requested/actual tiers.
- Add aggregate and recent request-log correlation rows to the live snapshot
  using persisted request-log fields only.

## Alternatives Considered

- Change the client-facing error code. Rejected because existing clients already
  understand `upstream_unavailable`, and the new classification is for
  operator diagnostics.
- Classify all timeout-shaped WebSocket failures the same way. Rejected because
  only opening-handshake timeouts prove the failure phase cleanly before any
  upstream response lifecycle starts.

## Risks

- Over-classification would make request-log summaries misleading. The
  implementation checks the explicit failure phase before changing the
  persisted error code.
- Diagnostic rows could grow noisy on high-volume deployments. The snapshot
  limits correlation groups and recent rows to bounded result sets.
