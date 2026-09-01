## Design

The bridge first performs the existing exact operation-fingerprint lookup and
then the existing unique latest-parent lookup. Only when those paths do not
produce a body-matching operation, and the parked-recovery flag is enabled, it
queries at most nine recent `UNKNOWN` rows for the same durable session and
model. The ninth row is a truncation sentinel; if present, recovery is
ambiguous and is rejected.

The query uses operation `created_at`, not mutable retry timestamps, so repeated
failed probes cannot keep an old operation eligible forever. The request body
is normalized with the existing operation-id and account-installation filters,
then compared byte-for-byte in canonical JSON form. A candidate must carry a
nonblank `parent_response_id`; parentless rows do not prove that a retry is a
continuation rather than a new first turn.

The production-facing durable session coordinator forwards both UNKNOWN
lookups to the repository. This keeps the submission path's capability checks
identical in tests and in the running service.

The operation's original fingerprint, operation id, and parent are rebound
before the durable `record_operation` call. Concurrent callers still converge
through the existing UNKNOWN claim fence. Ambiguous, stale, missing-body, and
feature-disabled cases continue to return the existing continuity failure.

The bounded lookup also emits finite-cardinality outcome diagnostics for
candidate absence, missing parents/request bodies, body mismatch, ambiguity,
and a single match. These diagnostics contain only candidate counts, a hashed
body digest, the body byte length, top-level JSON key names, an input
container summary, and the hashed bridge identity fields already used by the
structured logger; request payload values are never logged, and no diagnostic
outcome changes admission.
