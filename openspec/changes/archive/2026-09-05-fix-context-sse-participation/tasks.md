## Implementation
- [x] Require a parsed upstream event with a classified event type before recording HTTP participation.
- [x] Add route regressions for keepalives followed by EOF, error or a valid event.
- [x] Clarify prior validation counts and synchronize the owning spec.

## Verification
- [x] Run focused context and affected streaming tests, Ruff and strict OpenSpec validation.

Validation: the three new keepalive cases failed before the fix. After the fix, the context suite passed 28 tests and the affected streaming/retry suites passed 142 tests. Ruff, targeted type checking, the change and owning specification passed. Global strict spec validation reported the same 22 pre-existing failures as commit `12582f3`, with no new failures. Full CI is delegated to GitHub Actions.
