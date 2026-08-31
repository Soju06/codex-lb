## Context

`_reconnect_http_bridge_session` already collapses a live file pin,
`require_preferred_account`, and account-neutral recovery into
`required_preferred_account_id`. That value already disables preferred-account
fallback and fail-closes generic selection misses. Continuity-owner
provenance is still separate: selection receives
`preferred_account_is_continuity_owner=account_neutral_recovery`, and the
early typed `continuity_owner_unavailable` mapping is gated on the same
account-neutral flag.

A required file-pin or require-preferred owner therefore looks like an
ordinary preferred account inside selection. A miss is not typed
`continuity_owner_unavailable`, so it can be classified as a generic pool
miss instead of a restricted-owner miss.

## Goals / Non-Goals

**Goals:**

- Reconnect selection types a required owner as continuity provenance.
- Typed `continuity_owner_unavailable` maps to the existing required-owner
  unavailable envelope whenever that required owner exists.
- Movable soft `1011` reconnect without a required owner stays untyped.

**Non-Goals:**

- Create-path provenance, `affinity.py`, sticky writes, or fallback policy.
- Hard-session `1011` keep-owner behavior (`hard_close_account_bound`).
- Global owner rewrite, health-degraded semantics outside this reconnect
  selection call, or a new public error code.

## Decisions

- Set `preferred_account_is_continuity_owner` from
  `required_preferred_account_id is not None`. The three required-owner
  sources already collapse into that value; OR-ing the source flags again
  would drift from reconnect owner resolution.
- Gate the early `continuity_owner_unavailable` mapping on the same
  required-owner value instead of `account_neutral_recovery`.
- Leave `_http_bridge_reconnect_selection_failure` unchanged. Generic
  required-owner misses still fail closed with the existing envelope after
  bounded recovery; this change only types the selection call and maps the
  already-typed owner miss immediately.
- Add `preferred_account_overrides_single_account_routing` (default false).
  Honor it only together with required preferred ownership, and then skip the
  single-account narrowing branch. API-key assignment scope, security
  authorization, and typed continuity miss/policy-conflict handling stay on
  the existing continuity path.
- Reconnect sets that override only from `request_state.file_required_preferred_account`.
  Previous-response and account-neutral required owners remain typed continuity
  owners without the override, so they still intersect single-account policy.

**Alternative considered:** also type `hard_close_account_bound` sessions.
Rejected because hard `1011` already fail-closes through the hard-key path
and is out of this provenance seam.

**Alternative considered:** change create-path provenance in the same PR.
Rejected to keep the review on reconnect selection only.

**Alternative considered:** stop typing file-pin reconnect as a continuity
owner so single-account policy is unchanged. Rejected because the miss must
stay typed `continuity_owner_unavailable`; the override splits policy from
provenance instead.

## Risks / Trade-offs

- [Risk] Require-preferred reconnects that are not file-pin or
  account-neutral start sending continuity-owner provenance.
  → Mitigation: that is the intended contract; those callers already populate
  `required_preferred_account_id` and disable fallback.
- [Risk] Existing soft-`1011` tests do not yet assert the flag.
  → Mitigation: add the True/False assertions on the existing file-pin and
  movable tests before changing production code.

## Migration Plan

No migration. The change is reconnect-selection behavior only and can be
reverted by restoring the account-neutral-only flag and mapping gate.
