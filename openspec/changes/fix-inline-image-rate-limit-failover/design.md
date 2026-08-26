## Context

The direct streaming retry loop already excludes an account after a
pre-visible upstream rate-limit or quota error. Other pre-visible failover
paths also set `reallocate_sticky=True` before selecting the replacement.
The generic rate-limit branch omitted that update, allowing soft prompt-cache
affinity to keep selecting the failed account.

Inline image requests intentionally use the raw HTTP streaming path rather
than the HTTP bridge. Inline image data is self-contained and account-neutral;
unlike an uploaded `file_id`, it creates no account-owner requirement.

## Goals / Non-Goals

**Goals:**

- Permit a pre-visible, self-contained inline-image request to fail over after
  an upstream `429`.
- Keep soft affinity for successful requests while allowing it to move after a
  failed account is excluded.
- Preserve required owner and file-pin safety boundaries.

**Non-Goals:**

- Replaying output-visible requests.
- Moving requests with a resolved previous-response, turn-state, or file owner.
- Adding a configuration option or changing model eligibility.

## Decision

Set `reallocate_sticky=True` in the existing generic `failover_next` branch
immediately after the failed account is excluded. This matches adjacent
pre-visible retry branches and leaves the existing required-owner gates in
place. No sticky row is deleted; the normal replacement selection updates it
atomically when a new account is chosen.

## Risks / Trade-offs

- **Prompt-cache locality is lost for the failed attempt.** This is necessary:
  the account is already excluded for the current request, and retaining the
  affinity turns a recoverable limit into a client-visible failure.
- **Required owner could cross accounts.** Existing selection guards retain
  their required-owner checks; the regression uses an inline data URL rather
  than an uploaded file reference.

## Migration Plan

No migration is required. Deploy as an application-only proxy correction;
rollback is the previous image.
