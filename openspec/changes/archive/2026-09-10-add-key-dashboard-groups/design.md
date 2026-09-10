## Context

See proposal.md for motivation. Key-dashboard has independent Bearer authentication and allowlisted DTOs. Administrator key CRUD already handles write authorization, cache invalidation, and regeneration. Hourly usage rollups preserve history beyond request-log retention.

## Goals / Non-Goals

Reuse existing key management and rollup infrastructure. No self-enrollment, invitations, shared quota, proxy privilege changes, or access to peer request details.

## Decisions

- Store nullable indexed `api_keys.usage_group` rather than adding a group CRUD subsystem. One optional name in existing forms provides creation, assignment, and removal with no setup. Group names are exact and case-sensitive after trimming.
- Resolve membership in a key-dashboard repository from persisted caller ID, not cached authentication metadata. Read members and aggregate their retained hourly usage plus complementary raw windows sequentially on the request session.
- Use an explicit group DTO and return only masked member identities and four usage measures. Include inactive members' historical consumption so revocation does not erase the group's visible spend.
- Fetch on tab mount with AbortController and cleanup fencing, using existing cookie-free key request options. Derive group totals in the UI from the member rows instead of storing redundant totals.

## Risks / Trade-offs

- An administrator assigning a name opts all those keys into mutual visibility, including recent history before assignment. Explain this adjacent to the form field.
- Exact rolling windows can lose an unaligned partial-hour edge when raw retention has already pruned that edge. Reuse and document the established rollup semantics instead of claiming unavailable precision.
- Name-based groups have no empty-group lifecycle or bulk rename operation. Those are unnecessary for this scope.

## Migration Plan

Add the nullable indexed column in one revision after the current Alembic head; existing rows remain null. Verify upgrade, schema drift, and downgrade/re-upgrade in an isolated database. Production deployment remains a separate operator action.
