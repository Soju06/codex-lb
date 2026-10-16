## Context

Model source rows currently use one model slug for catalog, selection, billing and upstream payloads. `display_name` is cosmetic. Existing metadata already carries server-only request configuration and Codex capabilities.

## Goals / Non-Goals

**Goals:** stable client aliases; exact upstream IDs; preserved capabilities, tool schemas, pricing, ownership and source restrictions; usable dashboard create/edit flow.

**Non-Goals:** token pools, recursive alias resolution, subscription-model aliases, new WebSocket or compaction support, production configuration or deployment.

## Decisions

- Store a single optional `upstream_model` string in `raw_metadata_json`. Reuse existing persistence rather than add a nullable database column for an optional source request setting. Validate a nonblank string of at most 255 characters when present; absent means identity mapping. Strip this private setting from public catalogs.
- Keep `ModelSourceModel.model` as the public slug. Resolve the target only in the source HTTP forwarding layer, after selection and capability checks. Never mutate the request used for reservations, pricing or continuity. Do not recursively resolve a target that happens to match another alias.
- Rewrite only structured response model fields back to the alias. Never replace strings in text, tool arguments or errors. Streaming transformations must preserve framing semantics, bounded buffering and transport cleanup.
- Extend the existing Models field with `alias=upstream` entries, separated by commas/newlines. Bare IDs retain current behavior. Editing an existing upstream ID into an alias inherits its capabilities and pricing; editing unrelated fields preserves metadata. Explicitly removing the mapping restores identity forwarding.

## Risks / Trade-offs

- Ambiguous free-form entries → validate empty IDs, repeated separators and duplicate public aliases before submitting.
- Capability loss during rename → carry existing model settings by exact public ID first, then by the explicit upstream ID.
- Streaming regressions → only wrap alias streams; cover fragmented Unicode/SSE framing, tool contents and disconnect cleanup.
- Older replicas ignore alias metadata → deploy all replicas before configuring aliases; no live configuration changes are part of this task.

## Migration Plan

No database migration. Existing sources have no mapping and retain their behavior. Configure aliases only after rollout. Remove alias mappings before reverting to a version without this feature.
