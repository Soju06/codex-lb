## Context

See proposal.md. OwnershipScope records item IDs from upstream responses and expects request references to resolve durably across backends. Codex can independently assign an ID to function_call_output, which the upstream never emitted. Complete local call/result pairs already ignore bookkeeping IDs, but a namespaced retained call or an output-only continuation does not qualify as such a pair. The missing client ID then vetoes an otherwise correctly owned continuation.

## Goals / Non-Goals

Restore old conversations carrying client-generated result IDs using existing durable call ownership. Do not infer ownership from conversation headers, silently discard encrypted state, change source rotation, or relax subscription replay. No token changes or database backfill.

## Decisions

Add a strict tool-result bookkeeping classifier beside the existing client-message classifier, reusing current allowed fields, status, caller, output-content, metadata and account-scoped-state validators. Limit it to function_call_output, custom_tool_call_output and apply_patch_call_output with nonblank item ID and call_id. Reject unknown fields, hosted output kinds and opaque/file-backed state.

For qualifying request items, omit only id from the ownership-classification copy; preserve call_id lookup unless the existing complete ordered pair rule already proves the whole pair self-contained. Response-side publication and wire payloads stay unchanged. Unknown call IDs remain unknown; a known response anchor cannot authorize them. Reusing a result ID as an item_reference still requires ownership.

Reject the broader alternative of trusting one known reference for all unknown references, which could authorize another credential's encrypted content. Sticky routing alone would not resolve the confirmed missing tool-result ID.

## Risks / Trade-offs

- Client data could mimic an output envelope: strict shape/content validation and independently checked call_id constrain the exception; negative route tests cover unknown/mixed owners and opaque state.
- A future tool-result shape can still be declined: fail closed until its structure is supported explicitly.
- Mixed backend versions retain intermittent 409 until rollout finishes: deploy every backend with the HA surge script.

## Migration Plan

No schema or runtime setting changes. Verify SQLite/PostgreSQL route tests and independent review, then commit/push and deploy a clean release checkout. Check all serving images and readiness. Operational rollback remains explicitly requested through the deployment skill.
