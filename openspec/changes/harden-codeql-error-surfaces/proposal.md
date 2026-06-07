## Why

GitHub CodeQL reported several code-scanning alerts around path handling,
exception exposure, URL checks, and sensitive logging. Some findings are
already mitigated by existing runtime guards or are test-only false positives,
but the OAuth manual callback path can still return raw exception strings to
operators through dashboard responses.

## What Changes

- Harden dashboard OAuth manual-callback failures so unexpected exceptions are
  logged server-side and downstream clients receive a generic error message.
- Keep OAuth domain errors user-actionable by preserving their explicit
  `OAuthError` code and message.
- Add regression coverage for sanitized dashboard OAuth callback failures.
- Refactor CodeQL-triggering helper/test patterns where appropriate without
  changing proxy-compatible upstream error behavior.

## Impact

- **Code**: `app/modules/oauth/api.py`, `app/modules/oauth/service.py`,
  `app/main.py`, runtime logging helpers, and focused test helpers.
- **Tests**: OAuth API regression tests plus targeted existing unit tests.
- **Behavior**: unexpected internal OAuth callback errors no longer echo raw
  exception text in dashboard API responses.
