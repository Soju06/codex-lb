# Fix Chrome Apple Passwords TOTP autofill

## Problem

The dashboard's login TOTP control does not expose the standard one-time-code
metadata required for Chrome on macOS to offer and apply Apple Passwords
verification-code autofill.

## Requirements

- The login TOTP input MUST advertise `autocomplete="one-time-code"`.
- The input MUST retain numeric input semantics and the existing six-digit
  validation and auto-submit behavior.
- No authentication transport or secret handling changes are introduced.

Closes #2418.
