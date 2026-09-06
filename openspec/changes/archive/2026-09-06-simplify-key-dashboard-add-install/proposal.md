## Why

Key holders need a simpler overview and a direct way to configure their own Codex clients without manually copying endpoint and credential settings.

## What Changes

- Put existing key-dashboard content in the default Overview tab.
- Show only models in the policy presentation and use local, 24-hour lifecycle timestamps (`HH:mm:ss  dd/MM/yyyy`).
- Add an Install tab with macOS/Linux shell and Windows PowerShell setup scripts, copy/download actions, and authenticated curl commands using the current key.
- Configure the shared Codex home for App, CLI, and IDE extension, backing up existing configuration and credentials before replacing them.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-key-dashboard`: Simplified Overview presentation and authenticated, privacy-conscious client setup exports.

## Impact

Key-dashboard React components, translations, an authenticated installer endpoint, script generation and regression tests, and the owning OpenSpec context. No administrator navigation changes, new settings, deployment, database changes, or client software downloads.
