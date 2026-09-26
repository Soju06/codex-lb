## ADDED Requirements

### Requirement: Client setup redirects the built-in provider

Generated installers MUST write root `openai_base_url` with the same endpoint as the configured `codex-lb` provider. File-backed authentication MUST use the current exported key. Setup MUST preserve session/history files and project configuration, and MUST NOT change machine or user environment variables. The UI MUST explain restarting clients and the remaining scope of explicit profile/CLI overrides.

#### Scenario: Resume a built-in-provider chat after changing endpoint and key

- **GIVEN** a persisted chat created with the built-in OpenAI provider and an old endpoint/key
- **WHEN** the installer configures a new endpoint/key and the user resumes through the built-in provider
- **THEN** requests use the new endpoint and file-backed key
- **AND** inherited `OPENAI_BASE_URL` and `OPENAI_API_KEY` values do not redirect those requests on the supported client
- **AND** the old chat history remains available

#### Scenario: Keep project and environment configuration intact

- **WHEN** setup runs with inherited endpoint variables and a project-local `.codex/config.toml`
- **THEN** it writes the endpoint override to user configuration without modifying project files or persistent environment settings
- **AND** its uninstall restores the previous user config, including the previous endpoint override or its absence

### Requirement: Offline uninstall restores the original client setup

A successful installer MUST create a local credential-free uninstaller for its platform and show its invocation in terminal output and Install guidance. Uninstall MUST operate offline without a valid API key and resolve its Codex home from its own location. Setup MUST record the baseline before the first install managed by this version, retain that baseline across repeat installs, and back up current owned files before uninstall restores or removes them. Files originally absent MUST be absent after uninstall. Chat history and unrelated files MUST remain unchanged.

State and original backups MUST be validated before replacement, including schema version, allowed file names, path confinement, file types, and recorded hashes. Unsafe paths, malformed state, missing/corrupt required backups, or backup failures MUST abort without replacing client files. Missing lifecycle state MUST make uninstall a no-op. Uninstall MUST remove its lifecycle state only after successful restoration and MUST retain recovery backups.

#### Scenario: Reinstall then uninstall offline

- **GIVEN** an original configuration and auth plus an absent catalog
- **WHEN** the user installs, changes endpoint/key by rerunning setup, then runs the local uninstaller without network access
- **THEN** the original configuration and authentication are restored exactly and the catalog is removed
- **AND** the latest installed files remain recoverable in a private uninstall backup

#### Scenario: Preserve a fresh client's sessions

- **GIVEN** no original configuration, auth or catalog
- **WHEN** setup creates them, the user creates chats, and later uninstalls
- **THEN** the generated files and lifecycle state are removed and chat/session files remain

#### Scenario: Refuse damaged or unsafe restore state

- **WHEN** the state points outside the Codex home, a managed target or backup is a symlink/non-file, or an original backup is missing or changed
- **THEN** install and uninstall fail before replacing managed client files

#### Scenario: Repeat uninstall safely

- **WHEN** uninstall runs after its lifecycle state has already been removed
- **THEN** it exits successfully without changing any client files
