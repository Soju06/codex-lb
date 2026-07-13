## Why

Unary proxy surfaces such as Codex thread goals, Codex control calls,
transcription, and file create/finalize perform account token refresh before
opening the upstream request. If that refresh/connect step fails with a
transient transport error, the request previously failed immediately even when
another eligible account could serve it. Because no downstream-visible response
body has been emitted yet, these paths can safely exclude the failed account for
the current request and try another account within the request budget.

File finalization may be pinned to the account that owns the file. That strict
owner case must not fail over to a different account because the upstream file
handle is account-local.

## What Changes

- Treat retryable token-refresh/connect transport errors on pre-visible unary
  proxy surfaces as account-local transient failures.
- Record the failed account through normal proxy error handling, exclude it for
  the current request, and try one other eligible account when available.
- Preserve fail-closed behavior for strict account-owner requests, including
  pinned file finalization.
- Keep the existing request-budget checks around refresh/connect and upstream
  calls.

## Impact

- **Code**: `app/modules/proxy/service.py`
- **Tests**: `tests/unit/test_proxy_utils.py`
- **Behavior**: transient refresh/connect failures on one account no longer
  fail pre-visible unary requests immediately when another eligible account can
  complete them.
