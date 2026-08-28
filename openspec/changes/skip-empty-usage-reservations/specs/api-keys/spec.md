## ADDED Requirements

### Requirement: Limit-free admissions skip the reservation ledger

When API-key admission finds no applicable limit for a request (the key has no configured limits, or none of its limits apply to the request model), the system MUST NOT create a usage reservation row and MUST NOT run the reservation commit for that request. Admission MUST report that no reservation exists, and every downstream reservation consumer (stream and compact settlement, release paths, heartbeat touch, quota-planner warmup finalization) MUST treat the missing reservation as "nothing to settle" and no-op without error. Admission-time validity checks (key active, key not expired, lazy expired-limit reset) MUST still run unchanged. Because settlement — which records the key's last-used touch for reserved requests — never runs without a reservation, admission MUST record the last-used touch itself on the limit-free path so `last_used_at` continues to advance for these keys. Admission MUST also close the read transaction it opened before returning without a reservation, and MUST do so without expiring ORM state tracked by a caller-shared session (callers such as the quota-planner warmup service hold already-loaded rows on the same session and access them after admission). Keys with at least one applicable limit MUST continue to create reservations with per-limit items (including zero-delta items) and full commit durability.

#### Scenario: Key without limits creates no reservation

- **WHEN** admission runs for an API key with no configured limits
- **THEN** no usage reservation row is inserted and no reservation write is committed (the only commit issued closes the read-only admission transaction)
- **AND** the request is admitted without a reservation

#### Scenario: Key whose limits do not apply to the request model creates no reservation

- **WHEN** admission runs for a key whose limits all carry a `model_filter` that does not match the request model
- **THEN** no usage reservation row is inserted
- **AND** the non-matching limits' `current_value` values are unchanged

#### Scenario: Limit-free admissions still advance last-used

- **WHEN** admission runs for a key with no applicable limits
- **THEN** the key's last-used touch is recorded at admission via the write-behind coalescer
- **AND** the dashboard-visible `last_used_at` continues to advance for the key

#### Scenario: Settlement, release, and heartbeat no-op without a reservation

- **WHEN** a request admitted without a reservation finishes (success or failure)
- **THEN** settlement, release, and heartbeat-touch paths skip without error
- **AND** no settlement transaction runs for that request

#### Scenario: Quota-planner warmup probes without a reservation

- **WHEN** the quota-planner warmup executor admits its probe with a key that has no applicable limits
- **THEN** the warmup probe executes
- **AND** no reservation finalization is attempted

#### Scenario: Limit-free admission preserves shared-session ORM state

- **WHEN** a caller that holds already-loaded ORM rows on the same session (the quota-planner warmup service tracks the target account and decision) admits a request with a limit-free key
- **THEN** the admission read transaction is closed before admission returns
- **AND** the caller's tracked rows remain readable afterwards without reload errors, so the warmup probe executes

#### Scenario: Stale-reservation reclamation sees no rows for limit-free admissions

- **WHEN** stale usage-reservation reclamation runs after admissions for keys without applicable limits
- **THEN** those admissions contribute no reservations to reclaim

#### Scenario: Limited keys are unaffected

- **WHEN** admission runs for a key with an applicable limit
- **THEN** a reservation with per-limit items is created and committed exactly as before admission returned reservations unconditionally
