## Context

The PR already has a successful-completion grace and held-connection snapshot. Main now ships the private aiohttp cache and all its consumers, so the local history-preserving main merge retains those exact versions and leaves this change DB-only. The remaining grace helper duplicates the cancellation/deadline loop in `_shielded_bounded`.

## Goals / Non-Goals

**Goals:** Preserve successful versus failed/cancelled/pending teardown ownership, reuse the existing bounded wait, provide a real-worker starvation regression, and make normative/log claims match observed evidence.

**Non-Goals:** TLS changes, database pooling/schema changes, new cleanup registries, timeout tuning, PostgreSQL behavior changes or an explanation of historical long stalls.

## Decisions

- Implement `_teardown_completed_after_bound` by awaiting `_shielded_bounded(abandoned, _SQLITE_TEARDOWN_COMPLETION_GRACE_SECONDS)`. Its None result means successful completion; a returned task means still pending. Catch ordinary task failure and `asyncio.CancelledError` to return False. Passing the existing task to `ensure_future` preserves the same task owner. Do not create another wait loop or cancellation policy.
- Retain the existing0.75s grace and initial bound. Their roles are distinct: initial bounded observation, bounded opportunity for queued completion, then existing reclamation. No timing value is claimed to be optimal, and elapsed time at the first bound is not an event-loop lag measurement.
- Keep the pre-teardown held-connection snapshot. SQLAlchemy can clear `session._transaction` before finishing all connection releases, so failure/cancellation must not infer success from an empty live transaction reference. Preserve session fencing, driver interrupt/invalidation attempts, late task ownership and shutdown drain. Skip handles already closed; keep failure diagnostics.
- Replace contradictory helper comments and warning prose with observed outcomes: successful completion before reclaim, reclamation attempted, invalidation failed. A failed invalidation alone does not prove a permanent writer hold; do not claim guaranteed release merely because invalidation was attempted. Preserve stable log event names, phase, bound and elapsed fields.
- Extend `tests/unit/test_db_session.py` at the actual file-SQLite teardown seam. Existing late-completion tests defer coroutines; the missing distinct risk is a real aiosqlite worker completing while event-loop callbacks cannot run. Use a temporary real SQLite engine/session and observer-only wrapping of native rollback/close to signal worker completion. Block the loop past a short configured bound while the native operation finishes; assert the worker-finished witness precedes loop resumption and the teardown completion is observed in grace. No fake session rollback/close implementation.
- Assert no fence, no interrupt/invalidation, no deferred ownership and successful independent writer progress for successful completion; parameterize rollback/close. Since current PR already protects the behavior, obtain red sensitivity by disabling grace in the executable regression, then restore it. Run the same bounded scenario under asyncio and uvloop where supported, using the existing test loop conventions or a minimal isolated subprocess for the loop-policy distinction. Keep existing failed/cancelled/wedged and shutdown-owner cases; do not add a combinatorial outcome matrix.

## Risks / Trade-offs

- [Success inferred from terminality] → Consume the task result through the existing owner; failures/cancellation remain reclamation candidates.
- [False reclaim from scheduler starvation] → Real-worker witness and grace-disabled red sensitivity; avoid exact wall-time thresholds except bounded watchdog protection.
- [True wedge waits an extra0.75s] → Retain the existing explicit bound; no speculative tuning.
- [Documentation promises more than cleanup can prove] → Separate attempted cleanup from verified independent-writer progress and warning outcomes.

## Migration Plan

No migration. Merge pinned main normally on the existing PR branch; preserve both parent histories. Deploy/rollback through ordinary application restart after later validation and authorization. No rollout occurs in this phase.

## Open Questions

None blocking implementation. Phase-three artifacts intentionally leave the new helper/log/test remediation unimplemented.
