## Context

See [proposal.md](proposal.md) for the production incident. The shared classifier currently accepts canonical `code = "previous_response_not_found"`, or `code = "invalid_request_error"` only when `param = "previous_response_id"` and the message says the response was not found. The observed upstream frame has neither `code` nor `param`; normalization yields `code = "invalid_request_error"`, and its new ``Invalid `previous_response_id`.`` wording fails the message test.

Every downstream recovery mechanism already depends on this classifier. Direct WebSocket full resends retain a safe request body without the anchor and can replay transparently. Delta-only Codex-native requests receive a sanitized canonical code that the client uses to resend full local history; public `/v1` traffic receives generic `stream_incomplete` masking. The classifier miss bypasses all of those paths.

The incident data also contained upstream WebSocket interruptions, downstream disconnects, and connection-limit rotations. Those events explain why otherwise recent response ids can become unusable across connection boundaries, but they do not justify changing cleanup, retry, or transport policy here. The cleanup-budget and phase-attribution changes from upstream PRs #1723 and #1726 are already present on the affected deployment.

## Goals / Non-Goals

**Goals:**

- Recognize the exact newly observed stale-anchor envelope at the shared classification boundary.
- Preserve the existing safety gates that decide between transparent replay, client-assisted full resend, and fail-closed masking.
- Keep false-positive risk bounded with explicit code, parameter, and exact-message checks.

**Non-Goals:**

- Do not retry delta-only input without conversation history.
- Do not change WebSocket cleanup budgets, connection lifetime, account routing, health penalties, or retry-circuit policy.
- Do not infer that every generic invalid request is a stale anchor.

## Decisions

### Extend the shared semantic classifier

Add a normalized-message predicate for ``Invalid `previous_response_id``` with zero or one trailing period, and accept it only when the normalized error code is `invalid_request_error` and `param` is absent or already names `previous_response_id`. Reject other trailing punctuation and every different named parameter. Normalize `error.type` at the WebSocket rewrite helper just as its detection and retry-decision callers already do; without that consistency, the first classifier can recognize a code-less frame while the later rewrite still relays it raw. This keeps nested and top-level WebSocket consumers, the HTTP bridge, and compact/error sanitizers on one source of truth.

Alternative: special-case the raw frame inside the WebSocket relay. Rejected because it would duplicate semantics, miss other existing classifier consumers, and make nested versus top-level envelopes diverge.

### Reuse existing recovery policy unchanged

Once classified, the event follows the existing `previous_response_not_found` paths. A self-contained full resend can be replayed without the anchor; a delta-only request cannot. Codex-native clients receive the canonical sanitized code for their controlled full-history retry, while public clients retain generic masking.

Alternative: drop `previous_response_id` and retry every request. Rejected because the observed first and third failures carried only tool-call output deltas; replaying those without history would silently detach the tool result from its conversation.

### Treat connection churn as evidence, not patch scope

The rejected ids were successful on the same account and session 9–17 seconds earlier. That makes long-term retention and cross-account explanations less likely, but does not rule out short retention or reconnect invalidation. The deployment also recorded connection churn, which may make the stale-anchor condition more frequent, but the classifier repair remains correct whether the anchor was invalidated by a reconnect, upstream retention, or another server-side lifecycle boundary.

Alternative: combine this patch with #1711 transport changes. Rejected because #1711's cleanup warning is observational, its focused fixes are already merged, and the overnight data does not prove one new transport mutation that would eliminate all three failures.

## Risks / Trade-offs

- [Upstream reuses the exact message for malformed client ids] → The same recovery remains safe: transparent replay is still gated on a self-contained body, while delta-only clients receive a sanitized request to resend full history.
- [Over-classifying unrelated invalid requests] → Require `invalid_request_error`, reject any different named parameter, and match only the observed message after case/whitespace normalization and optional terminal punctuation.
- [Shared classifier changes non-WebSocket consumers] → Those consumers already treat unusable `previous_response_id` as continuity loss; focused tests cover the classifier plus Codex-native and public route behavior.

## Migration Plan

No data or configuration migration is required. Deploy as an application patch; rollback restores the prior raw-400 behavior without changing persisted state.
