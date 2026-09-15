# Review Follow-Up

The access-rejection status records a failed credential generation, not a
permanent prohibition on future credentials. Successful token rotation must
therefore reconcile that status in the same guarded write, regardless of which
write wins the race. It must not clear unrelated paused, disabled, or quota state.

A forced refresh that disables an account must remain disabling. For example,
`account_suspended` followed by fallback must not become `account_auth_invalidated`.

Reasoning is not redundant merely because its fields are recognized. An encrypted
reasoning item followed only by a new user message must fail closed. Projection
requires a complete retained assistant turn and independently replayable tool
state before another account receives the replacement.
