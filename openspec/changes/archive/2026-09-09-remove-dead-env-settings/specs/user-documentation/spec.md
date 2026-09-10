## MODIFIED Requirements

### Requirement: Generated settings reference stays in sync with the code

The documentation site SHALL include a settings reference page
(`docs/reference/settings.md`) generated from `Settings.model_fields` by
`scripts/generate_settings_reference.py`. The page SHALL list, for every
setting, its environment variable name — the `CODEX_LB_`-prefixed name, or,
for a setting declared with explicit validation aliases, the primary alias
followed by the remaining aliases — its type, and its default
(environment-derived defaults rendered symbolically), grouped by functional
area; it SHALL document the bare `PORT` special case, the
`CODEX_LB_ENV_FILE` bootstrap variable, and the `CODEX_LB_WORKERS_PER_INSTANCE`
startup guard (which is not a setting), SHALL list the process-level
environment variables codex-lb honors without making them settings
(third-party or POSIX conventions read by the launcher, libraries, or frozen
migrations), and SHALL list the removed (`_REMOVED_SETTINGS`) env names
sourced from the code. The generated page SHALL be
checked into the repository so the strict docs build stays hermetic, SHALL
carry a header identifying it as generated, and SHALL link the owning
OpenSpec capability. CI unit tests MUST fail when the checked-in page differs
from regenerated output, when the settings surface exceeds its ratchet
(lower-only without a simplicity-budget decision), or when an uncommented
`.env.example` assignment differs from the code default.

#### Scenario: Settings change without regeneration fails CI
- **GIVEN** a change to `Settings` fields in `app/core/config/settings.py`
- **WHEN** the unit test suite runs without regenerating `docs/reference/settings.md`
- **THEN** the regenerate-and-diff test fails until the page is regenerated and committed

#### Scenario: Reference page is reachable and generated
- **WHEN** a reader opens the published settings reference page
- **THEN** it is in the site navigation and linked from the Configuration page
- **AND** it identifies itself as generated from `scripts/generate_settings_reference.py`
- **AND** it links the owning OpenSpec capability

#### Scenario: Settings surface growth trips the ratchet
- **WHEN** the number of `Settings` fields exceeds the ratchet value
- **THEN** the ratchet unit test fails, forcing a simplicity-budget discussion before the surface grows

#### Scenario: Aliased setting renders every env name
- **WHEN** a setting is declared with validation aliases (for example `forwarded_allow_ips`)
- **THEN** the reference row shows the primary env name and each alias
- **AND** an operator can find the setting by either name

#### Scenario: Process-level conventions are documented but not settings
- **WHEN** codex-lb honors an environment variable that is a third-party or POSIX convention
- **THEN** the reference lists it in the process-level section with its consumer
- **AND** it is not counted toward the settings ratchet

#### Scenario: Removed names are listed without a deprecated-alias section

- **WHEN** the reference page is regenerated
- **THEN** its "Removed" section lists exactly the names in `_REMOVED_SETTINGS`
- **AND** the page has no deprecated-env-alias list
