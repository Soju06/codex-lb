## Context

Direct Responses WebSocket replay currently treats a request as replayable after `response.created` when no text delta has been exposed. The replay keeps the original downstream response id, suppresses the new upstream `response.created`, and forwards later replay events unchanged. Because a fresh upstream response restarts `sequence_number`, clients can observe a lower sequence within one response id and correctly reject the mixed generations.

## Goals / Non-Goals

**Goals:**
- Never expose two numeric sequence generations under one downstream response id.
- Preserve transparent replay when no numeric sequence-bearing frame has reached the downstream client.
- Settle the failed server-side attempt exactly once and give clients a generic transport-level retry signal when replay is unsafe.

**Non-Goals:**
- Renumber or synthesize replay sequence numbers.
- Deduplicate arbitrary text, reasoning, item, tool-call, or vendor events across upstream generations.
- Change HTTP bridge replay behavior.

## Decisions

Record the last top-level finite integer `sequence_number` after its direct WebSocket frame is successfully sent downstream. Suppressed events do not establish this watermark; non-integer values such as the native `"error"` sentinel remain outside the numeric contract.

Transparent replay eligibility will reject a pending request once that watermark exists. Sequence-free pre-created or created-only attempts retain the existing bounded one-shot replay behavior.

The same watermark gates event-triggered replay. Retryable quota failures, authentication failover, and security-work authorization retries remain eligible only before any numeric sequence has reached the client. If an upstream terminal error arrives after sequence exposure, codex-lb finalizes and forwards that terminal event without reconnecting; code 1011 is reserved for the upstream-close path where no terminal event exists.

When an upstream close reaches this specific replay refusal, codex-lb will remove and finalize the pending request without emitting a synthetic terminal event into the already-started response generation, then close the downstream WebSocket with code 1011. The generic abnormal close lets compatible clients retry the full request on a fresh transport while preventing a second sequence generation from entering the original stream. Existing cleanup releases the response-create admission, account lease, API-key reservation, and request log exactly once.

Renumbering replay frames was rejected. A numeric offset could satisfy monotonicity but cannot prove semantic continuity or prevent duplicated item, reasoning, tool-call, or side-effect events. Starting a second visible response id in the same client request was rejected because clients model one active response per request stream.

## Risks / Trade-offs

- Clients that do not retry abnormal WebSocket closes will see a transport interruption instead of codex-lb masking the upstream disconnect. This is safer than silently mixing response generations.
- The watermark must be committed only after a downstream send succeeds; counting suppressed replay-created or duplicate-tool frames would unnecessarily disable otherwise-safe replay.
- A downstream send failure takes the client-disconnect cleanup path and never reaches transparent upstream-close replay.
