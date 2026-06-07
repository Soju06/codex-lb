## ADDED Requirements

### Requirement: Persisted reset-window routing setting
Dashboard settings storage SHALL persist `prefer_earlier_reset_window` as a
non-null setting with allowed values `primary` and `secondary`. New and migrated
installations SHALL default the value to `secondary`.

#### Scenario: Existing dashboard settings are migrated
- **GIVEN** an existing dashboard settings row without `prefer_earlier_reset_window`
- **WHEN** migrations are applied
- **THEN** the row has `prefer_earlier_reset_window = "secondary"`

#### Scenario: Settings API rejects unsupported windows
- **WHEN** a settings update requests a reset-window value other than `primary` or `secondary`
- **THEN** the API rejects the payload instead of persisting it
