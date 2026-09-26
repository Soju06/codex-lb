## Why

The installer redirects only the custom `codex-lb` provider. Existing chats that use the built-in `openai` provider can still target the old/default endpoint after the API key changes. Setup also has no offline uninstall to restore the prior endpoint, credentials and catalog together.

## What Changes

- Set root `openai_base_url` to the same endpoint as `model_providers.codex-lb.base_url`.
- Handle inherited `OPENAI_BASE_URL` through Codex's explicit config precedence; retain environment values and project files.
- Install a credential-free local uninstaller and expose its command in Install guidance and terminal output.
- Track the first pre-install snapshot across repeat installs; uninstall backs up current files then restores that snapshot, including file absence, without deleting chats.
- Validate restore state and backups before replacing files, and keep uninstall offline and independent of API key validity.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-key-dashboard`: Redirect built-in-provider resumed chats and provide reversible offline client setup.

## Impact

Client installer generation, local lifecycle state and uninstall scripts, Install guidance, and regression coverage. No database/API schema changes, server settings, global environment edits, session rewrites or project configuration rewrites.
