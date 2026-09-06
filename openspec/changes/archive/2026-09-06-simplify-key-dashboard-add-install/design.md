## Context

The standalone key dashboard already holds a validated credential in memory and isolates its requests from administrator authentication. See proposal.md for motivation.

## Goals / Non-Goals

Deliver a small self-service configuration flow with no new dependencies or global settings. Do not download Codex binaries, modify the operator host, change account routing, or claim to install a desktop App on unsupported operating systems.

## Decisions

- Use accessible Radix tabs, defaulting to Overview. Unmount Install on departure to discard secrets and pending fetches. Keep existing usage/profile/log contracts unchanged.
- Format lifecycle with an explicit local date-fns pattern. Show only enforced/allowed model names in the profile policy section.
- Generate scripts in one pure backend module behind mandatory self-service authentication. Browser copy/download fetch the same text as curl, avoiding divergent templates. Endpoint responses use `private, no-store` and `Vary: Authorization`.
- Use the request origin and the existing `/backend-api/codex` compatibility route. Configure the OpenAI-named custom provider (`codex-lb`, responses, websocket support, requires_openai_auth) and file-backed `auth.json`, so GUI clients do not depend on terminal environment variables.
- Back up and replace config.toml/auth.json, with an explicit UI notice. Arbitrary TOML merging would need a new cross-platform runtime/parser and risk silently corrupting existing sections. Backups preserve user recovery; other Codex files stay untouched.
- Encode TOML strings with valid ASCII escapes (including Unicode scalar escapes) and auth data with JSON serialization; shell uses quoted heredocs, PowerShell uses literal here-strings. Models and credentials never become executable text. Unix uses private umask; Windows applies current-user-only ACLs to new files and the backup directory without changing unrelated files or home-directory ACLs. Use unique backups and stop if existing files are symbolic links or not regular files.
- Select the enforced model, otherwise the first allowed model. Unrestricted keys omit the model field so the installed Codex client's default remains authoritative, avoiding a separately maintained default slug.
- Key-containing previews are masked; only explicit copy/download outputs contain the real credential. Clipboard and downloaded files remain user-controlled and cannot be revoked by Disconnect, so the UI warns against sharing them and notes shell-history exposure.

## Risks / Trade-offs

- Replacing configuration changes unrelated client preferences → clearly disclose this and print backup location without secrets.
- Plaintext credentials → private files/directories, no-store exports, explicit copy/download, masked previews.
- WSL/remote IDE extension uses another home → explain running the Linux script in that environment.
- Project-level Codex configuration or managed login restrictions may override user configuration → explain restart and policy limitations in context; no destructive edits to workspace settings.
- Windows runtime may not be available on the development host → execute Bash installers in isolated test homes and validate Windows content/encoding; report any missing runtime verification.
