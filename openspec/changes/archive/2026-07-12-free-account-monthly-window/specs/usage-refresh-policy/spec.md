## ADDED Requirements

### Requirement: Free-account quota normalizes to a monthly window

When upstream usage or rate-limit payloads report a single free-account quota window as `primary_window.limit_window_seconds == 2592000` with no `secondary_window`, the system SHALL normalize that payload as a monthly-only quota window rather than as a primary 5h window or a secondary 7d window.

#### Scenario: Monthly free-account payload becomes monthly-only
- **WHEN** usage refresh or rate-limit payload mapping receives `primary_window.limit_window_seconds = 2592000`
- **AND** `secondary_window` is `null`
- **THEN** the system records and exposes the quota as a monthly-only window
- **AND** it does not synthesize a 5h primary or 7d secondary window for that account

### Requirement: Free-account quota capacity applies only to the monthly window

The system SHALL treat the free-account monthly window as the only free-account quota capacity window for overview and summary calculations.

#### Scenario: Free account contributes only monthly quota capacity
- **WHEN** the system computes quota capacity for a free account with a normalized monthly-only window
- **THEN** the free account contributes capacity to the 30d monthly window
- **AND** the free account contributes zero 7d quota capacity

### Requirement: Weekly semantics are not inferred from the primary slot alone

The system SHALL NOT infer weekly secondary semantics solely because a primary-slot payload reports `limit_window_seconds == 604800`.

#### Scenario: Primary-slot weekly duration does not trigger implicit secondary mapping
- **WHEN** a payload includes a primary-slot window whose `limit_window_seconds` is `604800`
- **THEN** downstream interpretation is determined by the normalization rules for that account shape
- **AND** the system does not automatically treat that primary-slot payload as a secondary weekly window only because of that duration
