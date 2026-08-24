## Context

The HTTP Responses bridge may retain the last completed response ID and a fingerprint of its stored input prefix. Current session-level optimization injects that response ID when an incoming request matches the prefix, including requests that already contain a full resend. Production showed that the retained response ID can become invalid even though the full resend remains self-contained.

## Goals / Non-Goals

**Goals:**

- Keep verified self-contained full resends unanchored at the session-level optimization.
- Keep their complete input intact while unsafe cumulative prompts remain anchored.
- Preserve existing anchor behavior for non-full-resend continuations.

**Non-Goals:**

- Change durable-anchor verification, quarantine, account ownership, or fresh-bridge recovery.
- Change client-supplied `previous_response_id` handling.
- Change cleanup, retry, account health, or database behavior.

## Decisions

- Classify the effective payload once beside the existing prefix-match check and exclude only verified self-contained full resends from session-anchor injection.
  - This keeps the decision at the boundary that would otherwise inject and trim the payload.
  - The verification reuses the existing replay projection, retained-output proof, and pending-tool-call proof.
  - The existing classifier treats any multi-item sequence or a single item of at least 4 KiB as a possible full resend; the safety proof prevents that broad classifier from suppressing required anchors.
- Preserve the existing session-anchor candidate and account-ownership checks for non-full-resend requests.
  - Moving the rule into durable lookup or account selection would change unrelated recovery paths.
- Retain a structured `session_anchor_injection_skipped` bridge event for the full-resend decision.
  - This makes production verification possible without logging request content.

## Risks / Trade-offs

- A full resend that omits prior output cannot safely continue without its anchor. → Require retained-output or exact pending-tool-call proof and cover the unsafe control.
- Full resends may send more input than an anchored, trimmed request. → Accept the input cost because the request already supplies continuity and correctness takes priority.
- Existing upstream recovery code may change around this seam. → Keep the patch local to the candidate predicate and verify the public route plus current beta-4 lifecycle tests.
