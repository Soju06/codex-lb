## Context

The Codex-native `/backend-api/codex/responses` WebSocket path masks upstream `previous_response_not_found` and emits `codex_previous_response_stale` in a terminal `response.failed`, intending the client to soft-reset and retry without the anchor. Observed behavior (pi 0.82.1 / pi-ai 0.82.1) is that the client recovers by matching the error *code*: its one-shot retry fires on `error.code == "previous_response_not_found"` and nothing else, then reconnects and resends full context without `previous_response_id`. The proxy-specific `codex_previous_response_stale` code matches no client recovery path, so the turn ends and only a full client restart recovers.

## Goals / Non-Goals

**Goals:**
- Make stale-anchor continuity loss recoverable by unmodified Codex clients on the WebSocket route.
- Preserve the hygiene intent of masking: no raw upstream error envelope and no missing response id downstream.
- Preserve semantic classification: clients can still tell stale-anchor loss apart from quota, policy, auth, and generic invalid-request failures.

**Non-Goals:**
- No client changes, no HTTP bridge changes, no public `/v1` changes, no routing changes.
- `upstream_unavailable` and suppressed-duplicate `stream_incomplete` are out of scope (follow-up).

## Decisions

- **Signal with the canonical `previous_response_not_found` code, sanitized.** It is the only application-level signal unmodified clients act on, and it is the code the route's own upstream uses, so it stays faithful to the Codex contract. The raw upstream envelope and the missing `resp_...` id are still stripped.
- **Refine the masking requirements to their intent.** The masking spirit is "do not leak the raw upstream error object or internal ids," not "never emit the bare code." Change the requirements from forbidding `previous_response_not_found` outright to forbidding the raw envelope and id, while permitting the sanitized canonical code on the Codex-native route.
- **Deliver as an application-level error, not a transport close.** The client full-resend cascade (five full-payload resends, then a sticky WebSocket->HTTP downgrade) is triggered by transport signals (`1009` close, `413`), per the ingress-budget ops notes, not by an application-level `previous_response_not_found`. An application-level canonical code triggers the client's controlled single full-context retry instead.
- **Keep public `/v1/responses` on `stream_incomplete`.** OpenAI-compatible clients do not expect the Codex continuity code; the canonical-code signal is scoped to the Codex-native route.

## Rejected Alternatives

- **Keep the nonstandard classifier (`codex_previous_response_stale`).** Current behavior; unmodified clients do not recognize it, so recovery never fires.
- **Protocol close frame (1011-style).** A transport signal, so on the official client it risks the single-message full-history resend and the `1009`/`413` retry-then-permanent-HTTP-downgrade cascade the masking exists to avoid.
- **Forward the raw upstream envelope/id.** Violates the masking hygiene intent and can leak internal ids.
- **Teach the client to recognize the proxy code.** A proxy-specific adaptation the client project would correctly reject; the deviation is on the proxy, so the fix belongs on the proxy.

## Load-bearing assumption

The official Codex client recovers from an application-level `previous_response_not_found` with a controlled single full-context retry (drop the anchor, resend once), the same recovery the reference pi / pi-ai transport performs, and does not treat it as a transport-level cascade trigger. The reference-client behavior is confirmed from source; the official-client behavior should be confirmed by the maintainers before merge.

## Risks / Trade-offs

- Reverses a deliberate masking decision. Mitigated by keeping the id and raw envelope stripped and scoping the canonical code to the Codex-native route only.
- If any client keys on the exact `codex_previous_response_stale` string it loses that signal. No such client is known; the code was proxy-specific and undocumented as a client contract.
- Recovery is a full-context resend, unavoidable once connection-scoped continuity is lost. codex-lb already slims oversized history to the WebSocket budget (or fails fast with `400 payload_too_large`), so this introduces no new size hazard.
