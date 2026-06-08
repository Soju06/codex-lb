## ADDED Requirements

### Requirement: Clipboard copy utility supports secure and fallback paths
The frontend clipboard utility SHALL use `navigator.clipboard.writeText` when available in secure contexts and SHALL fall back to `document.execCommand("copy")` when the Clipboard API is unavailable or blocked.

#### Scenario: Secure context uses Clipboard API
- **WHEN** copy is requested in a secure context with `navigator.clipboard.writeText` available
- **THEN** the utility writes text using `navigator.clipboard.writeText`

#### Scenario: Blocked secure-context copy keeps a synchronous fallback path
- **WHEN** copy is requested in a secure context and `navigator.clipboard.writeText` later rejects
- **THEN** the utility still attempts `document.execCommand("copy")` within the same user interaction

#### Scenario: Non-secure context uses execCommand fallback
- **WHEN** copy is requested and `navigator.clipboard.writeText` is unavailable or rejected
- **THEN** the utility uses a hidden textarea and `document.execCommand("copy")`

### Requirement: Fallback copy supports explicit container scoping
The fallback clipboard path SHALL accept an optional DOM container and SHALL mount the temporary textarea under that container so copy remains functional inside focus-managed regions such as dialogs.

#### Scenario: Dialog-scoped fallback mounts textarea inside dialog
- **WHEN** copy is requested with a dialog container
- **THEN** the fallback textarea is appended inside that dialog container before `execCommand("copy")` runs

#### Scenario: Fallback container cleanup always runs
- **WHEN** fallback copy completes or throws
- **THEN** the temporary textarea is removed from the provided container
