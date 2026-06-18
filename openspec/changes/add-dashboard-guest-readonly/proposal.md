## Why

Issue #673 asks for a way to share dashboard subscription and status visibility without giving every viewer full admin control. The accepted direction is a small internal abstraction that can grow into multi-user permissions later, instead of coupling the feature to one-off route checks.

## What Changes

- Add a dashboard `guest` role with read-only permissions alongside the existing full-control `admin` role.
- Add persisted settings for enabling guest access and for an optional guest password.
- Allow guest access without a guest password when explicitly enabled.
- Add a guest login endpoint for password-protected guest access.
- Require dashboard write permission on mutating dashboard routes while preserving read access for GET routes.
- Surface role and permissions in the dashboard session response so the UI can hide or disable write controls for guests.

## Impact

- Operators can share dashboard overview, accounts, API key metadata, request logs, usage, firewall state, settings, and sticky-session state in read-only mode.
- Guests cannot import accounts, start OAuth, pause/reactivate/delete accounts, mutate settings, manage API keys, change firewall entries, or delete sticky sessions.
- Existing admin password, TOTP, trusted-header, disabled-auth, and local bootstrap behavior remains compatible.
