## Context

The existing durable full-resend proof checks the exact stored prefix and response-bound tool manifest. Its context classifier uses the stricter account-neutral projection, which rejects native metadata and some otherwise complete tool-loop histories. The new proof remains request-local and bound to the same durable identity.

## Goals / Non-Goals

Recover complete HTTP tool loops on their original account, preserving the original wire request. Do not introduce account relocation, agent-message recognition, quota behavior, settings or migrations.

## Decisions

- Add a pure HTTP-specific context verifier. Use the existing namespace normalization and tool-manifest checker on a temporary view. Remove native metadata from that view only after checking it is an object. Skip shape-valid historical developer controls only within the fingerprint-verified prefix, and completed shape-valid assistant preambles only before the complete tool batch.
- Feed the result into the immutable durable full-resend proof. Do not feed it into the account-neutral classifier. At stale-anchor recovery require the proof still matches the payload/durable row and the active session account is its owner.
- For a proved owner-bound resend, disable implicit preferred-account fallback and owner retirement. Existing explicit account-neutral recovery retains its independent validation.
- Reuse existing operation fencing, no-visible-output and single-replay limits. The verification view never becomes an upstream payload.

## Related work

Compared against main `72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e`. PR #2581 adds agent-message suffix recognition and equivalent owner guards; this change excludes agent messages and addresses native tool metadata, assistant preambles and historical controls. Shared owner-guard hunks need reconciliation if either lands first. PR #2617 widens account-neutral prefix-settled replay, which is not changed here. Issue #2582 and its timeline identify #2581 as its proposed fix; this change does not claim to close it.

## Risks / Trade-offs

- A classification view can accidentally erase safety-relevant structure. Validate shapes before skipping items, retain exact manifest matching and test malformed, missing, duplicate and fresh-control cases.
- A preferred account is not a required account without fallback controls. Test a selectable alternate with the durable owner unavailable, including quarantined anchors.
- Tests use synthetic requests and isolated storage. Existing private deployment observations do not establish this upstream candidate's runtime behavior.

## Migration Plan

No data migration or configuration change. Reverting the scoped code returns to conservative rejection of these histories.
