### Requirement: Direct Docker fallback uses the image startup contract

The direct Docker fallback for running the current checkout MUST use the image default startup script instead of overriding the command. The documented and helper-driven direct Docker path MUST keep the default HTTP responses session bridge deployment at one worker unless the operator disables the bridge.

#### Scenario: direct Docker command keeps migration startup behavior

- **WHEN** an operator runs the documented direct Docker fallback
- **THEN** the container uses the image default startup script
- **AND** the startup script performs the migration handoff before launching the app

#### Scenario: direct Docker helper recreates the live container shape

- **WHEN** an operator runs the local Docker helper
- **THEN** it rebuilds the current checkout image
- **AND** it recreates `codex-lb-direct` with the documented localhost ports and named data volume
- **AND** it does not run `git pull` or otherwise mutate the current branch
