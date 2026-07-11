## 1. Contract and schema

- [x] 1.1 Define workspace and seat identity as separate concepts.
- [x] 1.2 Add a forward migration for nullable per-seat identity storage.

## 2. Implementation

- [x] 2.1 Capture and persist seat identity during OAuth and token refresh.
- [x] 2.2 Carry the selected local account through server-side OAuth flow state.
- [x] 2.3 Verify the returned seat and replace only the selected row.
- [x] 2.4 Remove persistent sticky bindings, durable bridge aliases, and durable
      bridge continuity anchors after permanent authentication failure.
- [x] 2.5 Pass the selected account from the dashboard reauthentication action.

## 3. Verification

- [x] 3.1 Cover matching and mismatched Team seats sharing one workspace.
- [x] 3.2 Cover dashboard targeted OAuth request payloads.
- [x] 3.3 Run backend lint, type checks, and targeted tests.
- [x] 3.4 Run frontend type checks and targeted OAuth tests.
- [x] 3.5 Validate a single-head migration against an upgraded database copy.
- [x] 3.6 Validate the OpenSpec change strictly.
