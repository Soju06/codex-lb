# add-opencode-auth-export

## Why
Operators who use codex-lb as their account onboarding dashboard currently need to authenticate a ChatGPT/OpenAI account in codex-lb and then repeat the OpenAI OAuth flow in OpenCode. This duplicates setup work even though codex-lb already stores the account tokens encrypted after import or OAuth.

## What Changes
- Add a dashboard-only export action that emits an OpenCode-compatible `auth.json` payload for one selected account.
- Export one account at a time so users can intentionally choose which account is written into OpenCode's `openai` provider slot.
- Keep the downloaded `auth.json` limited to the official OpenCode OAuth fields: `type`, `refresh`, `access`, `expires`, and `accountId`.
- Show account metadata and explicit secret-handling copy in the dashboard without adding non-official fields to the exported file.

## Impact
- Users can authenticate once in codex-lb, export a selected account, and copy the resulting payload into OpenCode's `~/.local/share/opencode/auth.json`.
- The feature exposes raw refresh/access tokens only to authenticated dashboard users and logs the export action without recording token material.
