## ADDED Requirements

### Requirement: Key deactivation records its reason

`PATCH /api/api-keys/{id}` with `isActive: false` SHALL set `deactivated_reason='manual'`, also on a key the owner cascade already turned off (an explicit revoke wins over the cascade, so `reactivate-keys` never resurrects it). `isActive: true` on an inactive key SHALL clear `deactivated_reason`, and MUST be refused with `409 owner_disabled` while the key's `owner_user_id` points to a `status=disabled` account; the owner's status MUST be read under a lock in the same transaction as the write (SQLite write-intent transaction, PostgreSQL `FOR UPDATE` on the owner row) so a concurrent disable cannot leave an active key on a disabled account.

#### Scenario: Manual revoke survives the owner's reactivation

- **GIVEN** a key turned off by its owner being disabled
- **WHEN** an operator revokes it explicitly, the owner is re-enabled and `reactivate-keys` runs
- **THEN** the key stays inactive with reason `manual`

#### Scenario: Keys of a disabled owner cannot be re-enabled

- **WHEN** `isActive: true` is patched on a key whose owner is disabled
- **THEN** the response is `409 owner_disabled`
