## ADDED Requirements

### Requirement: Dashboard settings must expose upstream proxy routing controls
The settings dashboard MUST allow operators to inspect upstream proxy routing state, enable or disable routing, choose the default proxy pool, create proxy endpoints, create proxy pools, and add endpoints to pools.

#### Scenario: Operator creates a pool from existing endpoints
- **GIVEN** the upstream proxy admin API returns at least one endpoint
- **WHEN** an operator creates a pool and selects endpoint members
- **THEN** the dashboard MUST call the pool creation API with the selected endpoint ids
- **AND** refresh the displayed upstream proxy admin state.

### Requirement: Dashboard accounts must expose account proxy bindings
The accounts dashboard MUST allow operators to bind an account to a proxy pool and disable an existing account binding.

#### Scenario: Operator binds an account to a pool
- **GIVEN** upstream proxy routing has at least one proxy pool
- **WHEN** an operator selects a pool for an account and saves the binding
- **THEN** the dashboard MUST call the account binding API for that account
- **AND** display the selected pool as the account binding.
