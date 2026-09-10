## Context

See proposal.md for motivation. Initial candidate is `2ce9335614`; the pinned
target is `5794d8d7a`. Upstream archives the original ownership change and adds
source dispatch plus dashboard resilience binding. The canonical Responses
ownership requirement and the upstream source-dispatch requirements remain
authoritative. Prior candidate verification is historical, not composition proof.

## Goals / Non-Goals

Preserve both accepted behaviors without changing overflow activation,
conversation scoping, replay policy, or account-deletion visibility. Do not
replace source lifecycle ownership with test doubles in positive route proofs.

## Decisions

Keep the enum-valued ownership resolver and boolean wrapper next to the new
overflow selector. Choosing one side wholesale either returns a boolean from
the enum resolver or removes the upstream selector.

Resolve subscription continuity before source admission and dispatch. A recorded
`resp_*` owner stays on its subscription account even when a source serves the
model. The same anchor without a recorded owner may reach that source and
finalize one reservation. Wrapping construction/claim spies, real request logs,
ASGI responses, and a local upstream prove the distinction. Existing upstream
tests retain cancellation, admission, limited-key estimate, and cleanup proof.

Keep compact's moved ownership and settlement block. Transplant only the
dashboard resilience binding after its moved settings read. Keeping both blocks
would duplicate cleanup and restore the old owner-count behavior. The forwarded
receiver still leaves failed pre-acknowledgement reservations with the origin;
deferred health and cancellation handling remain behind confirmed settlement.

## Risks / Trade-offs

The current-head review also exposed marker-only HTTP owner misses. The raw
stream and session bridge required a previous-response identifier before their
existing cardinality check. The route-level matrix reproduces the gap with
zero and multiple assigned owners while sole-owner controls pass. Extend those
same checks to a client-supplied unregistered marker, without treating a fresh
proxy-injected placeholder as a continuation. Registered aliases and file pins
still select their independent owner; confirmed model sources stay on source
routing before subscription bridge admission. Keep the existing sanitized 502
and assignment-only count. For example, an echoed `turn_example` without an
anchor cannot choose between two assigned accounts merely because one is ready.
Registered live aliases also remain independent owner proof when their durable
alias is absent. The bridge reuses the API's resolved owner, or performs the
same scoped lookup for a direct service caller, before deciding that the marker
is ownerless. Real bootstrap-and-echo tests cover that local-only case; a guard
based solely on the durable lookup would incorrectly reject a known owner.

Direct WebSocket marker-only reconnects enter turn-state ownership lookup but
previously skipped source classification and sole-owner counting because those
checks required a previous-response identifier. A client-supplied unregistered
`turn_example` with no anchor must follow the existing compatibility contract:
resolve source ownership, then count assignment-scoped owners for subscription
routing. This closes an implementation gap in the canonical Responses
requirement; it does not change conversation ambiguity or marker portability.
Proxy-generated first-turn placeholders remain ordinary first turns. Registered
aliases and independent file/required owners remain authoritative. Confirmed
source ownership keeps HTTP fallback, and an unavailable source lookup keeps
the existing subscription fallback. Test both direct routes with zero, one, and
multiple owners, assignment scoping, and these controls before republishing.

Provider portability keeps its stricter generated-marker shape check separate
from subscription marker compatibility. A readable `turn_example` marker can
use the subscription sole-owner fallback, but that does not make its body safe
to move to another provider. The portability predicate retains upstream's
32-lowercase-hex marker rule. Its closed decline reason remains
`turn_state_bound`; overflow activation and subscription compatibility are
unchanged. The two upstream assertions exposed this coupling in hosted CI.

- Text composition can hide lifecycle regressions. Run route-level ownership
  cases plus existing source-dispatch and compact-settlement controls.
- Removed environment settings can survive in older tests. Keep their values
  on the existing dashboard snapshot, not a new configuration fallback.
- A passing earlier head does not clear this one. Bind local reviews and hosted
  checks to the final composition; retain old failures and reviews separately.

## Migration Plan

The `0da41b64e` composition adds upstream agentic HTTP promotion and captured
automation reclaim budgets. Its sole text conflict joins the new
`http_continuation_signal` import with the existing security-exhaustion constant;
both are required. Structured history makes a request eligible for a transport,
not a new account owner. Inferred locality remains soft and cannot inject an
anchor or trim history. For example, a promoted history carrying an unknown
previous response still needs the existing source check and assignment-only
owner count. Confirmed sources continue through source dispatch before bridge
admission. Verify public routing and reservation handoff along with retry and
queue controls. The upstream automation claim-budget column and migration stay
unchanged; test their reclaim and migration cases without accessing live data.

No migration or deployment is part of this reconciliation. Publish the reviewed
composition with an exact expected-head lease. Preserve the previous commit and
its evidence as the recovery point. Inherited upstream migrations are unchanged.

## Current-main and header-validation reconciliation

The c0beaaadd composition retains upstream constantized core and session-bridge tunables. It must not resurrect removed settings while retaining the candidate ownership checks. The unfinished correction tasks previously appended to the archived ownership change are tracked here until their verification and delivery are complete.

Validate the added synthesized-turn-state context field with the existing bridge metadata control-character rule before constructing signed headers. This closes a gap in the established safe-header contract, without changing marker recognition or ownership. Both legacy-forward alias lookups must retain their expected arguments.

### Compact cleanup after persistence failure

The existing compact reservation-cleanup requirement in `openspec/specs/api-keys/spec.md` requires cleanup for every compact exception. Settlement and fail-safe release remain shielded. If both fail, keep `reservation_released=False` and the established `usage_settlement_failed` error, suppress health writes, and leave the accounted durable reservation for existing stale reclamation. The follow-up `bound-compact-failed-cleanup` change replaces unbounded detached retries with this exceptional recovery contract.
