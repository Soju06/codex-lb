## Why

The dashboard Conversations view exposes conversation-content-level data to
guest principals. Field masking is insufficient because guests should not be
able to discover or query conversation membership or details at all.

## What Changes

- Require an admin dashboard principal for `/api/conversations` collection and detail routes.
- Hide the Conversations option from the dashboard view selector for guests.
- Resolve guest `view=conversations` deep links to Request Logs without mounting or querying the conversation view.
- Preserve existing admin conversation behavior and the already protected conversation archive routes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: Make conversation list/detail reads admin-only.
- `conversations-api`: Add the admin-principal route boundary.
- `frontend-architecture`: Hide the conversation dashboard surface from guests.

## Impact

The change affects one FastAPI router, its integration authorization coverage,
the dashboard view selector, and the dashboard page's view/query selection.
Conversation response schemas, repository semantics, archive routes, and admin
behavior remain unchanged.
