## ADDED Requirements

### Requirement: Usage-cap routing state follows every standard usage write

After any standard usage refresh persists new usage, account selection and reused HTTP or WebSocket connection admission MUST observe the resulting cap state before admitting another turn. A refresh that persists no usage MUST NOT trigger a cap-state refresh.

#### Scenario: Usage endpoint crosses a cap

- **GIVEN** an account's standard usage is below its configured cap in cached routing state
- **WHEN** a usage endpoint refresh persists usage at or above that cap
- **THEN** fresh account selection excludes the account
- **AND** reused connection admission rejects an ordinary new turn on that account

#### Scenario: Usage endpoint persists no change

- **WHEN** a standard usage refresh writes no usage row
- **THEN** it does not refresh or invalidate usage-cap routing state

### Requirement: Trusted WebSocket rerouting precedes reused-account cap enforcement

For an unpinned trusted-capability turn on a reused WebSocket, the proxy MUST apply capability authorization and account replacement before enforcing the reused account's usage cap. A capped ordinary account MUST NOT prevent the turn from moving to an authorized uncapped account. If the authorized reused account remains selected and is capped, the proxy MUST reject the turn with `account_usage_cap_reached`.

#### Scenario: Trusted turn leaves a capped ordinary socket

- **GIVEN** an unpinned WebSocket is connected to an ordinary account that has become usage-capped
- **AND** another authorized uncapped account is available
- **WHEN** the client sends a trusted-capability turn
- **THEN** the proxy retires the ordinary socket and routes the turn to the authorized account
- **AND** it does not return `account_usage_cap_reached` for the retired ordinary account

#### Scenario: Authorized reused account is capped

- **GIVEN** a trusted-capability WebSocket remains reusable on its authorized account
- **WHEN** that account is usage-capped before the next turn
- **THEN** the proxy rejects the turn with `account_usage_cap_reached`
