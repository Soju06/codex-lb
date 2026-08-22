## ADDED Requirements

### Requirement: Permanent refresh failure preserves request eligibility

Permanent refresh credential or session errors MUST mark the account `reauth_required`. This requirement refines existing refresh-failure requirements: any instruction to remove that status from request routing, tear down affinity, or add a process-local unavailable overlay MUST defer to the canonical status matrix in `account-routing`. Proactive and background refresh MUST continue to skip known-bad refresh material.

A separate upstream account-deactivation signal MUST continue to mark the account `deactivated` and apply existing hard-unavailable behavior.

#### Scenario: Refresh failure separates access and refresh eligibility

- **WHEN** refresh-token exchange fails permanently without an account-deactivation signal
- **THEN** the account becomes `reauth_required`
- **AND** proactive refresh stops
- **AND** ordinary requests may still use the stored access token

#### Scenario: Account deactivation remains hard-unavailable

- **WHEN** upstream reports that the account itself is deactivated
- **THEN** the account becomes `deactivated`
- **AND** it is removed from request routing and affinity

### Requirement: Claimless forced refresh reconciles fresh account state before exchange

When refresh coordination is unavailable or omitted, forced refresh MUST freshly re-read the account before exchange. A genuinely changed refresh-token fingerprint MUST cause the caller to adopt the peer row without exchange. Unchanged plaintext under new ciphertext MUST use the fresh ciphertext as the compare-and-set guard. Unchanged material in `reauth_required` or `deactivated` state MUST fail permanently without exchange.

#### Scenario: Same material uses the fresh guard

- **GIVEN** the fresh row contains the same refresh-token plaintext under different ciphertext
- **AND** the fresh status is non-terminal
- **WHEN** claimless forced refresh runs
- **THEN** successful rotation is persisted against the fresh ciphertext guard

#### Scenario: Genuine peer rotation is adopted

- **GIVEN** the fresh row contains a different refresh-token fingerprint
- **WHEN** claimless forced refresh runs
- **THEN** the peer row is adopted without upstream exchange or persistence

#### Scenario: Unchanged terminal material fails closed

- **GIVEN** the fresh row remains `reauth_required` with the same refresh-token fingerprint
- **WHEN** forced refresh runs
- **THEN** it fails permanently without exchanging the token
