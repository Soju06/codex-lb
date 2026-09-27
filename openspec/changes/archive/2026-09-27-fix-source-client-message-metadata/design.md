## Context

Request 2930c6b8-6190-43aa-9e8f-a5b6b69f3120 was correlated to a fresh Codex 0.157.1 conversation: five user/developer messages, each with a local ID and metadata containing turn_id, create_time and content_item_kinds. There was no prior upstream response. The existing validator accepts only turn_id, leaving those IDs incorrectly subject to ownership lookup.

## Goals / Non-Goals

**Goals:** Consistent direct-source classification of the observed metadata across all already-supported input envelopes, with identical forwarding and ownership enforcement.

**Non-Goals:** Inferring legacy ownership, changing source selection, stripping history, changing subscription account replay or response serialization.

## Decisions

- Create one classification-only projection that validates the exact metadata field set, then retains only turn_id in a shallow item copy. Both direct-source ownership extraction and portability use it before their existing item validators. The original body is never changed.
- Keep the shared account-neutral metadata validator unchanged. Broadening it globally would change subscription replay policy as well as source routing.
- Preserve malformed metadata unchanged so existing fail-closed checks remain effective. Never remove item IDs, calls or encrypted content in this projection; existing typed item predicates continue to decide whether those fields are neutral.
- Accept finite numeric timestamps excluding booleans and a list of nonblank string content kinds, including an empty list. These are descriptive client metadata, not upstream object handles.

## Risks / Trade-offs

- Metadata projection might accidentally hide owned state → validate all keys and types first, preserve every other field and cover malformed, file and encrypted-state mutations.
- Ownership extraction might differ from portability → call the same projection at both entry points and cover real route continuations across replicas.
- Old chats have unknown reasoning even after this fix → retain rejection; their explicit original-source alias remains the recovery path.

## Migration Plan

No schema migration. Test and review in an isolated clean checkout, then use the existing active-active HA surge deployment. Verify fresh synthetic messages through the pooled public model and across all three backends.
