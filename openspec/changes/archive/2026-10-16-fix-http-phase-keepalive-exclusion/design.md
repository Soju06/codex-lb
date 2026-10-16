## Context

See proposal.md for the failure. The HTTP byte parser preserves noncanonical SSE field layouts. The existing `sse_event_type_from_block` intentionally recognizes only canonical frames because its callers use it to authorize verbatim relay.

## Goals / Non-Goals

**Goals:** Enforce keepalive exclusion at the single HTTP phase observation owner for all accepted SSE framing.

**Non-Goals:** Change downstream event classification, canonical relay eligibility, response ownership, or retry policy.

## Decisions

- Keep `_HTTPPhaseLatencies.parse_event` as the sole phase observation choke point. Wire strings cannot encode the exclusion in their type; the runtime guard must examine both payload type and event field.
- Extract the effective SSE event field using the existing SSE line-boundary and field-value parsing rules. The last event field wins. Add a separate extractor rather than broadening the canonical relay helper.
- Check the event field only when an observation is still needed. Preserve lazy clock access and the later-token fast path.
- Extend the existing route/persistence/metrics test with one header-only keepalive case. Add one property test at the phase owner for framing and header/payload representation equivalence, a distinct risk that the single route example cannot cover.

## Risks / Trade-offs

- Misclassifying a payload while checking its header could change downstream behavior. The phase guard will leave the returned payload and payload-derived event type untouched, and the property test will assert that contract.
- A canonical-only header check would miss accepted upstream forms. Generated cases will include CR, LF, CRLF, optional space, and field ordering.
