## Context

The proxy writer and database model already preserve five credential-safe
route diagnostics. The request-log read model stops at a narrower Pydantic
schema, and the frontend has a second Zod boundary before the existing details
dialog. Operators need the same persisted values across both boundaries.

## Goals / Non-Goals

**Goals:**

- Carry the five existing route fields through the API and frontend unchanged.
- Show populated values in the existing request details dialog.
- Keep the presentation compact and omit absent metadata.

**Non-Goals:**

- Change route selection, persistence, or database schema.
- Expose proxy URLs, usernames, passwords, or headers.
- Add table columns, filters, settings, or navigation.

## Decisions

- Extend the existing `RequestLogEntry` and `RequestLogSchema` instead of
  adding a second endpoint or nested routing object. This matches the flat
  persisted model and existing request-log response.
- Render metadata only in the details dialog. Routing diagnostics are useful
  during investigation but too sparse for permanent table columns.
- Reuse the details grid's existing label/value rows and localized strings.
  Boolean fallback state is rendered as localized yes/no text.

Alternative considered: expose only the fail-closed reason. That would leave
successful fallback and endpoint selection unobservable, so all five
credential-safe fields move together.

## Risks / Trade-offs

- [Risk] Internal identifiers add visual noise → Render only non-null values in
  the opt-in details dialog.
- [Risk] Future fields accidentally expose secrets → Explicitly whitelist the
  five existing credential-safe columns rather than serializing route objects.
- [Risk] Mixed-version deployments omit fields → Keep every added field
  nullable and optional in the frontend parser.

## Migration Plan

The API change is additive and reads existing columns, so no migration or
backfill is needed. Older frontends ignore the new keys; newer frontends accept
responses from older backends. Rollback removes the response/UI fields without
changing stored data.

## Open Questions

None.
