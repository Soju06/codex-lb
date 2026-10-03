## ADDED Requirements

### Requirement: Newly authenticated accounts automatically enroll in limit warm-up

When browser OAuth, device-code OAuth, or auth.json import creates a new local account, the system MUST initialize its `limit_warmup_enabled` flag from the `limitWarmupAutoEnableNewAccounts` dashboard setting read when credentials are persisted. The setting MUST default to false. Credential replacement or reauthentication of an existing account MUST preserve its stored flag. Enrollment MUST NOT change global warm-up settings or send a probe during authentication/import; background warm-up MUST retain all existing eligibility and deduplication checks.

#### Scenario: OAuth creates an enrolled account

- **GIVEN** automatic warm-up enrollment is enabled
- **WHEN** browser or device-code OAuth successfully creates a new local account
- **THEN** the account is persisted with limit warm-up enabled
- **AND** dashboard account listings return `limitWarmupEnabled: true`

#### Scenario: Auth import creates an enrolled account

- **GIVEN** automatic warm-up enrollment is enabled
- **WHEN** an authorized auth.json import creates a new local account
- **THEN** the account is persisted with limit warm-up enabled
- **AND** dashboard account listings return `limitWarmupEnabled: true`

#### Scenario: Disabled enrollment affects future accounts

- **GIVEN** automatic warm-up enrollment is disabled
- **WHEN** browser OAuth, device-code OAuth, or auth.json import creates a new account
- **THEN** that account has `limitWarmupEnabled: false`
- **AND** existing account preferences remain unchanged

#### Scenario: Credential replacement preserves account preferences

- **GIVEN** an existing account has limit warm-up explicitly enabled or disabled
- **WHEN** OAuth reauthentication or auth.json import replaces that account's credentials
- **THEN** the stored warm-up preference remains unchanged regardless of the enrollment default

#### Scenario: Global disable still prevents warm-up traffic

- **GIVEN** global limit warm-up is disabled and automatic enrollment is enabled
- **WHEN** OAuth or auth.json import creates an enrolled account
- **THEN** the global switch remains disabled
- **AND** background usage refresh does not send warm-up traffic for that account

### Requirement: Operators configure new-account warm-up enrollment in dashboard settings

The dashboard settings API MUST expose the persisted boolean `limitWarmupAutoEnableNewAccounts` through GET and PUT. Omitted updates MUST preserve its value. Settings → Routing → Warm-up MUST offer an accessible switch with text explaining that it affects only newly authenticated or imported accounts. The switch MUST remain editable when global warm-up is disabled, respect write permissions and busy state, and preserve unrelated settings. Migration MUST initialize the setting to false without changing any account's warm-up flag.

#### Scenario: Save and reload the enrollment default

- **WHEN** an operator disables automatic enrollment in Settings
- **THEN** the settings update persists false and a subsequent GET returns false
- **AND** an unrelated settings update does not re-enable it

#### Scenario: Global warm-up is independent

- **GIVEN** global warm-up is disabled
- **WHEN** an operator changes the automatic enrollment switch
- **THEN** the new enrollment default is saved without enabling global warm-up

#### Scenario: Existing installation upgrades safely

- **GIVEN** existing settings and accounts with mixed warm-up preferences
- **WHEN** the database upgrades
- **THEN** the new settings value is false
- **AND** existing account preferences and global settings are unchanged
