# Context

Workspace availability and request retryability are separate decisions. A revoked workspace cannot serve the selected request, but another eligible workspace may serve an account-neutral request rejected before any output is visible. Use a distinct `account_unavailable` failure class rather than calling a deactivated workspace a transient server failure or exhausted quota.

Recognize only the captured structured code; HTTP 402 alone may describe a payment or service incident and is not proof of a terminal account state. Reuse the existing permanent-failure persistence, selection invalidation and reservation settlement paths instead of adding a separate pause scheduler or a new configuration knob.

For example, HTTP 402 with `{"error":{"code":"deactivated_workspace","message":"Workspace has been deactivated"}}` from workspace A excludes A. A fresh, account-neutral Responses request may then complete through B. A request containing encrypted reasoning owned by A must instead retain its original error and never send that reasoning to B.

No production credentials, account identifiers, request logs or deployment addresses are needed to reproduce the behavior. Tests use synthetic accounts and stubbed upstream responses.
