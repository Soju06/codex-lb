## Implementation

- [x] Add nullable request-log archive lookup column and migration.
- [x] Persist the original request context id alongside downstream response ids.
- [x] Expose `archiveRequestId` from the request-log API.
- [x] Use `archiveRequestId ?? requestId` in the dashboard archive panel.
- [x] Cover repository, API, HTTP stream, WebSocket, migration, schema, and UI fallback behavior with tests.
