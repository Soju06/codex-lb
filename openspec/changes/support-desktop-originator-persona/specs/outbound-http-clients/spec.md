## ADDED Requirements
### Requirement: OAuth authorize requests use a configurable originator persona
Browser OAuth authorize requests MUST include an `originator` query parameter. The service MUST default that parameter to `codex_cli_rs` and MUST let operators override it through configuration when they need a different first-party Codex persona.

#### Scenario: default OAuth authorize originator stays CLI-compatible
- **WHEN** the operator does not configure an override
- **THEN** the browser OAuth authorize URL includes `originator=codex_cli_rs`

#### Scenario: configured OAuth authorize originator uses a Desktop persona
- **WHEN** the operator configures the OAuth authorize originator as `codex_chatgpt_desktop`
- **THEN** the browser OAuth authorize URL includes `originator=codex_chatgpt_desktop`
