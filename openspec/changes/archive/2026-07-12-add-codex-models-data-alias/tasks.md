## 1. Spec And Regression Coverage

- [x] 1.1 Add an OpenSpec delta for the `/backend-api/codex/models` `data` alias.
- [x] 1.2 Add integration coverage proving the Codex-native `models` payload remains present.
- [x] 1.3 Add integration coverage proving `data` is OpenAI-style and excludes Codex-hidden entries.

## 2. Implementation

- [x] 2.1 Add `data` to the Codex models response schema.
- [x] 2.2 Reuse the `/v1/models` model-list item mapping for the compatibility alias.
- [x] 2.3 Preserve existing Codex-native `models` behavior.

## 3. Verification

- [x] 3.1 Run targeted model endpoint tests.
- [x] 3.2 Run OpenSpec validation for this change.
