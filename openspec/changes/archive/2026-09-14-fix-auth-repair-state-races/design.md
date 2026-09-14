## Context

A snapshot is not evidence of credential replacement. Its SELECT can complete before rejection commits while publication finishes before the local mark. Only an explicit same-account repair can invalidate the captured repair generation.

## Decisions

- Retain per-account repair generations across snapshot refresh/reset. Snapshots still reconcile existing marks from committed routing state; they do not suppress marks that have not landed.
- Restore reset-based routing exclusion when repaired rejected credentials carry an unexpired cooldown. Preserve pause, deactivation, pending deletion, and unchanged-credential fencing. Avoid adding another stored status or encoding state in error text.
- Pass existing encryptors through their owning call paths, avoiding a new application-wide crypto cache or configuration surface.

## Verification

Deterministic event barriers exercise stale SELECT publication followed by a committed rejection mark, and the converse same-account repair ordering. Database-backed rotation and selection exercise cooldown retention and expiry. Independent reviews distinguish the original pinned head from the resulting patch. CI evidence is recorded separately from local verification.

## Scope

Do not rewrite competing PRs or split this PR without a maintainer choice. Do not update the local bundle or running service as part of this review follow-up.
