# clipboard-copy-fallback Specification (delta)

This delta tightens the existing `clipboard-copy-fallback` requirement so that
operator-facing copy controls MUST use the shared `copyToClipboard` utility
(or a component that wraps it). The OAuth account-add dialog's "Copy URL"
button currently calls `navigator.clipboard.writeText` directly with `void`,
swallowing rejection and showing no feedback — this delta closes that gap.

## MODIFIED Requirements

### Requirement: Operator-facing copy controls use the shared clipboard utility

The frontend SHALL provide copy controls for operator-facing text (authorization
URLs, tokens, IDs, etc.). Each such control MUST invoke the shared
`copyToClipboard` utility (directly or via a component like `<CopyButton>` that
wraps it). The control MUST NOT call `navigator.clipboard.writeText` directly,
because that path has no `document.execCommand("copy")` fallback and no error
feedback when the Clipboard API rejects (non-secure context, blocked permission,
dialog focus loss, etc.).

#### Scenario: OAuth dialog Copy URL button uses the shared utility

- **GIVEN** the "Add Claude account via OAuth" dialog is open
- **AND** the dashboard has received an `authorization_url` from
  `POST /api/claude/oauth/start`
- **WHEN** the operator clicks the "Copy URL" button
- **THEN** the click handler invokes `copyToClipboard(authorization_url, …)`
  with a dialog-scoped container
- **AND** if `navigator.clipboard.writeText` rejects (e.g., in a non-secure
  context), the utility falls back to `document.execCommand("copy")` on a
  textarea scoped to the dialog
- **AND** on success the operator sees a success toast; on hard failure the
  operator sees an error toast

#### Scenario: Existing happy-path copy still works

- **WHEN** copy is requested in a secure context with `navigator.clipboard.writeText`
  available
- **THEN** the utility writes text using `navigator.clipboard.writeText`
- **AND** no visual regression: the button still shows the copy icon and label

(Inherited scenarios from the existing `clipboard-copy-fallback` capability
remain unchanged: `Secure context uses Clipboard API`, `Blocked
secure-context copy keeps a synchronous fallback path`, `Non-secure context
uses execCommand fallback`, `Dialog-scoped fallback mounts textarea inside
dialog`, `Fallback container cleanup always runs`.)
