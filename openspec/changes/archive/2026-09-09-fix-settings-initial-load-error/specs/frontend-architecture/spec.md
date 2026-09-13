MUST make a failed initial Settings load actionable instead of showing an endless skeleton.

## ADDED Requirements

### Requirement: Settings initial load failure is actionable

The Settings page SHALL render its page-wide loading skeleton only while the
initial settings request is still pending and no settings data is available.

When the initial settings request reaches a terminal error and no settings data
is available, the Settings page MUST NOT render the loading skeleton. It MUST
render the settings error message, MUST announce that error through an alert
semantic, and MUST expose a keyboard-operable, accessibly named Retry action.
When the error carries no message of its own, the page SHALL render a settings
load-failure fallback message. Activating Retry SHALL refetch the settings
detail query without a full page reload, and Retry SHALL be disabled while that
refetch is in flight.

When settings data is available, a settings fetch error SHALL NOT hide the
settings form; the page SHALL keep the form rendered and surface the error
above it.

#### Scenario: Failed initial settings load replaces the skeleton

- **GIVEN** no settings data is available
- **WHEN** the initial settings request reaches a terminal error
- **THEN** the Settings page does not render its loading skeleton
- **AND** the settings error message is rendered and announced through an alert semantic
- **AND** an accessibly named Retry action is available

#### Scenario: Retry refetches settings in place

- **GIVEN** the initial settings request has failed and no settings data is available
- **WHEN** the operator activates Retry
- **THEN** the settings detail query is refetched
- **AND** no full page reload occurs

#### Scenario: Retry is disabled while the refetch is in flight

- **GIVEN** the initial settings request has failed and no settings data is available
- **WHEN** a settings refetch is in flight
- **THEN** the Retry action is disabled

#### Scenario: Pending initial settings load keeps the skeleton

- **GIVEN** no settings data is available and no settings error has occurred
- **WHEN** the initial settings request is still pending
- **THEN** the Settings page renders its loading skeleton
- **AND** no settings error message or Retry action is rendered

#### Scenario: Settings error with cached data keeps the form visible

- **GIVEN** settings data is available
- **WHEN** a settings fetch error is present
- **THEN** the settings form sections remain rendered
- **AND** the settings error message is rendered above them
- **AND** the loading skeleton is not rendered
