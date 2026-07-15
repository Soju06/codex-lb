# Tasks: persist dashboard OAuth flow state

## 1. Schema

- [x] 1.1 Add `OAuthFlowState` ORM model (`oauth_flow_states`) to `app/db/models.py`.
- [x] 1.2 Add Alembic migration on the current head
      (`20260713_040000_add_account_refresh_claims`) with upgrade + downgrade,
      dialect-agnostic DDL.

## 2. Repository

- [x] 2.1 Add `OAuthFlowRepository` (`app/modules/oauth/repository.py`) with
      create / get-by-flow-id / get-by-state-token / latest / set-status /
      delete-pending-device / purge-expired methods.
- [x] 2.2 Encrypt the PKCE verifier at rest; expose a typed record dataclass.

## 3. Service wiring

- [x] 3.1 Persist flow records at creation (browser + device).
- [x] 3.2 Write terminal status transitions to the DB.
- [x] 3.3 Make `oauth_status` DB-authoritative when a row exists.
- [x] 3.4 Hydrate the local store from the DB on manual-callback, browser
      callback, and device complete when the flow is missing locally.
- [x] 3.5 Allow injecting a distinct store per `OauthService` (two-replica tests).

## 4. Tests

- [x] 4.1 Two-replica simulation: start on store/session A, complete on B.
- [x] 4.2 Cross-replica status: originator sees success written by another replica.
- [x] 4.3 Migration upgrade/downgrade round-trip on SQLite.
- [x] 4.4 Existing oauth flow suite stays green.

## 5. Spec + validation

- [x] 5.1 Add delta requirement to `replica-operations`.
- [x] 5.2 `openspec validate persist-oauth-flow-state --strict` passes.
- [x] 5.3 `openspec validate --specs` stays green.
