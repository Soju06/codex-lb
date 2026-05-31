## Why

Dashboard API calls can return 401 after the browser still has a stale frontend
state from a password login that was waiting for TOTP. The current 401 handler
marks the user unauthenticated but leaves `totpRequiredOnLogin` set, so the
auth gate renders the TOTP dialog even though the password-authenticated
session is gone. Operators should be sent back to the password login form.

## What Changes

- Clear stale pending-TOTP state when the shared API client handles a 401.
- Clear the frontend `passwordSessionActive` flag at the same boundary.
- Preserve existing first-run bootstrap, trusted-header, and password-required
  state so other auth modes keep their current screen selection.

## Impact

- No backend API or session-cookie behavior changes.
- First-run bootstrap behavior is unchanged.
- Standard password auth falls back to login after an expired dashboard session.
