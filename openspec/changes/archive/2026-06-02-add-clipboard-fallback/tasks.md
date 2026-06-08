## 1. Clipboard utility fallback behavior

- [x] 1.1 Add a shared `copyToClipboard` utility that prefers secure Clipboard API and falls back to `execCommand("copy")`.
- [x] 1.2 Add optional container support for fallback textarea mounting and guaranteed cleanup.

## 2. Dialog-aware copy integration

- [x] 2.1 Update shared `CopyButton` to pass dialog container context into `copyToClipboard` when used inside dialogs.
- [x] 2.2 Update OAuth dialog authorization URL copy button to pass dialog container context into `copyToClipboard`.
- [x] 2.3 Ensure pointer-triggered copy interactions do not leave copy buttons focused.

## 3. Regression coverage and verification

- [x] 3.1 Add clipboard utility tests for secure copy, fallback copy, and container-scoped fallback behavior.
- [x] 3.2 Add component tests that assert fallback copy works in dialog-hosted copy buttons, including API key created dialog and OAuth dialog paths.
- [x] 3.3 Run targeted clipboard/copy frontend tests and TypeScript typecheck.
