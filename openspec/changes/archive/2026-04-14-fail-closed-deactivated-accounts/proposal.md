## Why
Upstream deactivated-account failures can currently leak through as routable accounts. In practice this means an account may continue to receive selection attempts after OpenAI has already deactivated it, which can stall traffic until an operator manually pauses or removes that account.

The failure mode is especially visible on the usage refresh path, where `HTTP 401` responses with a deactivation message were not consistently promoted into a terminal account status.

## What Changes
- Treat `account_deactivated` as a permanent failure code across account selection and recovery logic.
- Preserve upstream usage error codes so the usage refresh path can distinguish permanent deactivation from generic unauthorized responses.
- Fail closed on usage refresh errors that explicitly identify a deactivated account, including message-only fallbacks.
- Preserve the deactivated status in the dashboard/runtime model so the account leaves the routing pool automatically.

## Impact
- Deactivated upstream accounts stop receiving new traffic without requiring manual operator intervention.
- Generic `401 Unauthorized` responses that do not indicate account deactivation continue to follow the existing non-terminal flow.
- Dashboard and account APIs surface a first-class `deactivated` status with a deactivation reason for affected accounts.
