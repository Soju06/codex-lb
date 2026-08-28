# api-keys — replica-safe-reset-credits deltas

## MODIFIED Requirements

### Requirement: API keys can inspect and redeem reset credits within their account pool

The system SHALL expose `GET /v1/reset-credit` and `POST /v1/reset-credit` for API-key-authenticated self-service reset-credit access. Both routes MUST require a valid `Authorization: Bearer sk-clb-...` header even when `api_key_auth_enabled` is false globally. Validation failures MUST use the existing OpenAI error envelope used by `/v1/*` routes.

The target account pool SHALL be derived from the authenticated API key. If `account_assignment_scope_enabled=true`, only `assigned_account_ids` SHALL be eligible. If account scope is not enabled, all selectable accounts SHALL be eligible.

`GET /v1/reset-credit` SHALL return only credits for the authenticated key's eligible account pool. `POST /v1/reset-credit` SHALL reject requests whose `account_id` is outside that pool.

Before `POST /v1/reset-credit` decrypts and forwards the bearer token for the upstream consume call, the system SHALL refresh the target account with the normal account-token freshness rules and use the refreshed account credentials for the consume request.

If that self-service credential refresh fails, `POST /v1/reset-credit` SHALL stop before the upstream consume call, return a client-actionable conflict response, and keep using the existing `/v1/*` OpenAI error envelope.

After acquiring the cross-replica redeem claim, `POST /v1/reset-credit` SHALL re-validate the requested `redeem_id` against a live upstream reset-credits fetch performed inside the serialized redeem section using the refreshed account credentials, regardless of whether the replica-local snapshot lists the credit as available. It MUST NOT consume a credit solely on the basis of the replica-local snapshot, because a peer replica may have redeemed that credit while this request waited for the claim and the local snapshot can remain stale until its invalidation poll fires. The upstream fetch response is authoritative: if the credit is available upstream the redemption proceeds; otherwise the endpoint returns 409 and replaces the cached snapshot for that account with the fresh upstream snapshot.

On a successful `POST /v1/reset-credit` redemption, the system SHALL invalidate the redeemed account's cached reset-credit snapshot, force a usage refresh for that account, and invalidate account-selection cache state when that usage refresh writes updated usage. A failed or empty post-redeem usage refresh SHALL NOT roll back the successful credit redemption response.

#### Scenario: Missing API key is rejected

- **WHEN** a client calls `GET /v1/reset-credit` or `POST /v1/reset-credit` without a Bearer token
- **THEN** the system returns 401 in the OpenAI error format

#### Scenario: Invalid API key is rejected

- **WHEN** a client calls `GET /v1/reset-credit` or `POST /v1/reset-credit` with an unknown, expired, or inactive Bearer key
- **THEN** the system returns 401 in the OpenAI error format

#### Scenario: Scoped API key sees only assigned accounts

- **WHEN** an API key has account scope enabled with assigned accounts
- **AND** the client calls `GET /v1/reset-credit`
- **THEN** the response includes reset-credit entries only for those assigned accounts

#### Scenario: Unscoped API key can read the full selectable pool

- **WHEN** an API key has account scope disabled
- **AND** the client calls `GET /v1/reset-credit`
- **THEN** the response may include reset-credit entries for any selectable account that currently has an available cached credit

#### Scenario: Out-of-pool account is rejected on redeem

- **WHEN** a client calls `POST /v1/reset-credit` with an `account_id` outside the authenticated API key's eligible pool
- **THEN** the system returns 403 without redeeming any credit

#### Scenario: Self-service reset-credit works while global proxy auth is disabled

- **WHEN** `api_key_auth_enabled` is false and a client calls `GET /v1/reset-credit` or `POST /v1/reset-credit` with a valid Bearer key
- **THEN** the system still authenticates that key and applies the same account-pool rules

#### Scenario: Self-service redemption refreshes stale account credentials before consume

- **GIVEN** an eligible account has a redeemable reset credit
- **AND** the persisted access token for that account is stale but refreshable
- **WHEN** a client successfully calls `POST /v1/reset-credit` for that account
- **THEN** codex-lb refreshes the account before decrypting the consume bearer token
- **AND** the upstream reset-credit consume call uses the refreshed account credentials

#### Scenario: Self-service redemption surfaces refresh failures as conflicts

- **GIVEN** an eligible account has a redeemable reset credit
- **AND** that account's credential refresh fails before the upstream consume call
- **WHEN** a client calls `POST /v1/reset-credit` for that account
- **THEN** codex-lb returns a conflict response in the standard `/v1/*` OpenAI error envelope
- **AND** codex-lb does not call upstream reset-credit consume for that request

#### Scenario: Fresh replica redeems a credit missing from its local snapshot

- **GIVEN** a freshly started replica whose reset-credit snapshot store is empty for the target account
- **AND** upstream reports the requested `redeem_id` as available
- **WHEN** a client calls `POST /v1/reset-credit` for that account and `redeem_id`
- **THEN** the replica fetches the account's reset credits from upstream inside the serialized redeem section
- **AND** the redemption proceeds and succeeds instead of returning a false 409

#### Scenario: Credit already redeemed elsewhere returns 409 with a fresh snapshot

- **GIVEN** the replica-local snapshot does not list the requested `redeem_id` as available
- **AND** the authoritative upstream fetch reports that credit as unavailable
- **WHEN** a client calls `POST /v1/reset-credit` for that account and `redeem_id`
- **THEN** the endpoint returns 409 without calling upstream consume
- **AND** the fresh upstream snapshot replaces the replica's cached snapshot for that account

#### Scenario: Stale cached credit is re-validated after winning the claim

- **GIVEN** two replicas both cached the same reset credit as available
- **AND** replica A redeemed it while replica B waited on the cross-replica redeem claim
- **AND** replica B's cached snapshot still lists that `redeem_id` as available
- **WHEN** replica B wins the claim and processes `POST /v1/reset-credit` for that `redeem_id`
- **THEN** replica B performs the authoritative upstream fetch instead of consuming from its stale cache
- **AND** because upstream reports the credit unavailable, replica B returns 409 without sending a second upstream consume
- **AND** replica B replaces its cached snapshot with the fresh upstream snapshot

#### Scenario: Successful self-service redemption refreshes usage for immediate follow-up traffic

- **GIVEN** an eligible account has a redeemable reset credit and persisted usage/account state that still reflects a blocked window
- **WHEN** a client successfully calls `POST /v1/reset-credit` for that account
- **THEN** the redeemed account's cached reset-credit snapshot is invalidated
- **AND** codex-lb forces a usage refresh for that account before returning
- **AND** any account-selection cache entry derived from the stale usage state is invalidated when the refresh writes updated usage
- **AND** the response still returns the upstream `{code, windows_reset, redeemed_at}` success payload
