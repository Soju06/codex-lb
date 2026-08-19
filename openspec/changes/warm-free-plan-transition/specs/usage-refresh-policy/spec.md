## ADDED Requirements

### Requirement: Confirmed paid-to-Free transitions warm the new monthly window

When background usage refresh confirms that an opted-in active account changed
from a recognized paid plan to `free`, and that confirming refresh writes a
fresh monthly usage sample with a reset deadline and enough available quota for
the configured warm-up threshold, the system SHALL attempt one long-window
warm-up for that monthly quota window. Eligibility MUST NOT depend on the usage
percentage reported before the plan change.

The plan-transition exception SHALL apply only to an actual paid-to-Free change
confirmed by the refresh that wrote the monthly sample. It MUST NOT apply to a
single unconfirmed Free observation, an account that was already Free, or a
monthly sample left over from an earlier refresh. Ordinary same-window reset
detection MUST remain unchanged. The durable warm-up identity SHALL remain the
account, canonical `monthly` window, and monthly reset deadline. The confirming
monthly sample MUST report `used_percent < 100`; the configured minimum-
available threshold MAY impose a stricter lower usage limit.

#### Scenario: Confirmed paid-to-Free transition warms fresh monthly quota

- **GIVEN** an active opted-in account whose stored plan is a recognized paid plan
- **WHEN** background usage refresh confirms its transition to `free`
- **AND** that confirming refresh writes a monthly sample with a reset deadline and enough available quota
- **THEN** the system attempts one warm-up identified by the account, `monthly` window, and monthly reset deadline

#### Scenario: Previous usage percentage does not gate plan-transition warm-up

- **GIVEN** an active opted-in paid account whose previous selected quota sample was not exhausted
- **WHEN** background usage refresh confirms its transition to `free` and writes an eligible fresh monthly sample
- **THEN** the system attempts the monthly warm-up regardless of the previous usage percentage

#### Scenario: One unconfirmed Free observation does not warm

- **GIVEN** an active opted-in account whose stored plan is a recognized paid plan
- **WHEN** one background usage refresh reports `free` without satisfying downgrade confirmation
- **THEN** no plan-transition warm-up is attempted

#### Scenario: Already-Free account does not use the plan-transition exception

- **GIVEN** an active opted-in account whose stored plan was already `free`
- **WHEN** background usage refresh writes its first monthly sample without confirming a plan change
- **THEN** no plan-transition warm-up is attempted

#### Scenario: Stale monthly history does not warm after a plan change

- **GIVEN** an active opted-in account whose transition from a paid plan to `free` is confirmed
- **WHEN** the latest monthly sample predates the confirming refresh
- **THEN** no plan-transition warm-up is attempted

#### Scenario: Existing durable identity deduplicates the transition warm-up

- **GIVEN** a warm-up attempt already exists for an account, `monthly` window, and monthly reset deadline
- **WHEN** the same confirmed paid-to-Free transition is evaluated again
- **THEN** no second warm-up request is sent for that durable identity
