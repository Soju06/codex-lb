# user-documentation Delta

## ADDED Requirements

### Requirement: Generated settings reference stays in sync with the code

The documentation site SHALL include a settings reference page
(`docs/reference/settings.md`) generated from `Settings.model_fields` by
`scripts/generate_settings_reference.py`. The page SHALL list, for every
setting, the `CODEX_LB_`-prefixed environment variable name, its type, and
its default (environment-derived defaults rendered symbolically), grouped by
functional area; it SHALL document the bare `PORT` special case and SHALL
list the removed (`_REMOVED_SETTINGS`) and deprecated env names sourced from
the code. The generated page SHALL be checked into the repository so the
strict docs build stays hermetic, SHALL carry a header identifying it as
generated, and SHALL link the owning OpenSpec capability. CI unit tests MUST
fail when the checked-in page differs from regenerated output, when the
settings surface exceeds its ratchet (115 fields; lower-only without a
simplicity-budget decision), or when an uncommented `.env.example` assignment
differs from the code default.

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
