## 1. Spec

- [x] 1.1 Add `responses-api-compat` delta for persisted `request_kind` and hashed `session_id` correlation on request logs
- [x] 1.2 Add `frontend-architecture` delta for rendering request kind and session correlation in the recent requests table

## 2. Backend

- [x] 2.1 Add `request_kind` and `session_id_hash` columns to `request_logs` via Alembic and base schema updates
- [x] 2.2 Persist `request_kind` and `session_id_hash` from proxy flows without storing raw `session_id`
- [x] 2.3 Expose the new fields through request-log repository, mapper, and API schemas
- [x] 2.4 Add regression coverage for Responses, compact, and websocket request-log rows
- [x] 2.5 Add request-log API filtering and facet options for `request_kind` and `transport`

## 3. Frontend

- [x] 3.1 Extend dashboard request-log schema/types with `requestKind` and `sessionIdHash`
- [x] 3.2 Render request kind and session correlation in the recent requests table while keeping legacy null rows safe
- [x] 3.3 Update frontend tests and mock factories for the new fields
- [x] 3.4 Add dashboard filter controls for `requestKind` and `transport` and wire them into request-log queries
