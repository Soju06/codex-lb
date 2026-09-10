# Verified probe recovery

The new proof is a completed request for the held model and service tier. Usage percentages remain advisory under the existing recovery rules. For example, a held Astra/default request can be recovered by a completed Astra/default probe; a Spark probe cannot clear it.

Generation fencing is necessary because two rejections can share blocked_at and reset_at down to the same stored second. Status writers conservatively advance the generation and discard scope unless they carry explicit rejected-request provenance. Migration leaves historical scope unknown. No inference from asynchronously written request logs is used.

The provider request is bounded to 30 seconds. A 60-second per-account database claim is renewed immediately before dispatch and checked at recovery. Competing probes receive 409, cancellation releases the claim, and expiry permits recovery from a crashed caller. Sessions remain request-owned.

Scope metadata describes the execution that can prove recovery, not a new model-scoped upstream penalty policy. Account-wide hold and ownership rules remain intact. All rejection writers must run the new version before operators rely on generation fencing in a multi-replica deployment.

The response separately reports HTTP status, completed execution and whether the guarded hold update landed. A later rejection can therefore leave accountStatusAfter limited even when an earlier guarded recovery did land. No credits are consumed or identity replaced by this endpoint.

Tracked by GitHub issue #2327. Review added two race regressions: a stale cached reimport must increment the database generation atomically, and a rejection writer must retain the generation returned by its own write rather than reading a later recovery generation. A timeout after response headers preserves the observed HTTP status while leaving completion and recovery false.
