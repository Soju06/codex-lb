# Per-key automatic Astra notes

## Why
Users currently maintain a local Codex model catalog to enable experimental history/notes only for Astra. Remote model discovery should carry the same opt-in without forcing it on other keys or requiring a static client catalog.

## What changes
- Add `auto_enable_astra_notes` to API-key create, edit, read, and persistence, default false for existing and new keys.
- Add a checkbox to the existing key dialogs with a description of its action.
- For opted-in keys, overlay notes activation only on an allowed, visible native `gpt-6-astra` catalog entry. Preserve upstream metadata and all other models.
- Off means no override. This preference neither revokes context access nor deletes notes or changes running sessions.

## Impact
API keys, their migration, native model discovery, the dashboard, and the existing Codex context-management documentation. This extends the same experimental context capability as PR #2102. No environment variable, new navigation item, or global setting is added.

