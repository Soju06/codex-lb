## ADDED Requirements

### Requirement: Fully unused Free quota receives one initial warm-up

When background usage refresh evaluates the selected long window for an active
account whose pre-refresh and current plan snapshots normalize to Free, with
global and per-account limit warm-up enabled, and no durable warm-up attempt
exists for that account, the system SHALL treat a fully unused monthly sample
written by the current refresh as a one-time initial warm-up candidate. The
sample MUST have canonical `monthly` window, `used_percent == 0`, and a reset
deadline. The resulting attempt MUST use the existing account, `monthly`
window, and monthly reset deadline identity. The no-prior-attempt condition
MUST be evaluated atomically with insertion across processes and replicas, and
MUST cover every warm-up window for that account.

This initial candidate MUST remain distinct from reset confirmation: a first
monthly sample or a sliding `reset_at` MUST NOT be classified as a confirmed
reset. The exception MUST NOT apply to partially used quota, stale monthly
history, an unknown or non-Free pre-refresh plan, an account with any prior
warm-up attempt, an unsafe account state, or an unselected long-window warm-up
path. Existing global, per-account, account status, model, and atomic-claim
gates SHALL continue to apply.

#### Scenario: First eligible refresh warms an unused already-Free quota

- **GIVEN** an active account was already on the Free plan when imported
- **AND** global and per-account limit warm-up are enabled with the long window selected
- **AND** the account has no prior warm-up attempt
- **WHEN** the current background refresh writes a monthly sample with `used_percent == 0` and a reset deadline
- **THEN** the system attempts one warm-up identified by the account, `monthly` window, and monthly reset deadline

#### Scenario: Import-time monthly history can be followed by initial warm-up

- **GIVEN** an already-Free account received a zero-use monthly sample during import while its per-account opt-in was disabled
- **AND** the operator later enables the account opt-in
- **WHEN** a later background refresh writes a current zero-use monthly sample and no prior attempt exists
- **THEN** the system attempts the one-time initial monthly warm-up

#### Scenario: Partial or stale monthly quota does not receive initial warm-up

- **GIVEN** an already-Free opted-in account has no prior warm-up attempt
- **WHEN** its monthly quota is partially used
- **OR** its latest monthly sample predates the current refresh
- **THEN** no initial monthly warm-up is attempted

#### Scenario: A previous attempt closes the initial path

- **GIVEN** an already-Free opted-in account has any durable warm-up attempt
- **WHEN** a later refresh reports a zero-use monthly sample with a different sliding reset deadline
- **THEN** no initial monthly warm-up is attempted
- **AND** ordinary confirmed-reset evaluation remains available for a future real reset

#### Scenario: A same-refresh attempt closes the initial path

- **GIVEN** an already-Free opted-in account had no attempt when refresh evaluation began
- **WHEN** another selected window creates or skips an attempt before the initial monthly candidate is claimed
- **THEN** no initial monthly warm-up is attempted

#### Scenario: Concurrent sliding deadlines admit one initial attempt

- **GIVEN** two replicas read no prior attempt for the same already-Free account
- **WHEN** both atomically claim current zero-use monthly samples with different sliding reset deadlines
- **THEN** exactly one initial monthly attempt is inserted and sent

#### Scenario: Rolling upgrades preserve claim serialization

- **GIVEN** an older PostgreSQL replica holds the existing per-window advisory lock and has an uncommitted warm-up attempt
- **WHEN** a new replica claims an initial warm-up for the same account
- **THEN** the initial claim MUST wait for the older transaction and reject insertion after its attempt commits, regardless of window or reset deadline
- **AND** ordinary new-replica claims MUST retain per-window serialization with older replicas
