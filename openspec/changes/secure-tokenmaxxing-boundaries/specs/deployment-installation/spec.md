## ADDED Requirements

### Requirement: Fresh setup avoids sample tilde database overrides

The setup command SHALL generate a fresh `.env.local` without a `CODEX_LB_DATABASE_URL` value that contains a literal `~` home-directory segment. A fresh setup MUST either omit the sample database URL override so settings can resolve the default data directory, or write an expanded absolute SQLite URL.

#### Scenario: Fresh setup omits the sample tilde database URL

- **GIVEN** `.env.local` is missing
- **AND** `.env.example` contains the sample `CODEX_LB_DATABASE_URL=sqlite+aiosqlite:///~/.codex-lb/store.db` value
- **WHEN** `bin/setup` generates `.env.local`
- **THEN** the generated file does not contain a database URL with a literal `~`
- **AND** omitted database URL configuration lets settings resolve the default data directory

#### Scenario: Existing local environment file is preserved

- **GIVEN** `.env.local` already exists
- **WHEN** `bin/setup` runs
- **THEN** the existing file is not rewritten
