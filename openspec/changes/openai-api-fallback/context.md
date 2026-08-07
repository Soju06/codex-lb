# Context

## Existing capability
codex-lb already persists OpenAI-compatible Model Sources in the shared database. Model Source API keys are encrypted with the installation `TokenEncryptor`, the dashboard never reads the stored plaintext key back, and the forwarding layer already implements `/responses` streaming/non-streaming calls, error redaction, usage extraction, cost accounting, and request logging.

The subscription account balancer separately classifies aggregate upstream quota exhaustion as `usage_limit_reached`. That classification is intentionally narrower than local account-capacity, fair-share, admission, authentication, and transient transport failures.

## Decision
Overflow reuses Model Sources rather than introducing a second provider/credential subsystem. At most one Model Source is designated as the subscription fallback. It must be enabled and Responses-capable. An optional fallback-model override lets the operator map subscription model requests to one configured source model; otherwise the requested model is preserved and must exist on the source.

The normal subscription path always runs first. Overflow is considered only after account selection reaches terminal aggregate `usage_limit_reached`. A configured fallback therefore does not participate in ordinary account balancing and does not become sticky primary capacity.

## Replay safety
A fresh request may cross to the external source only when its forwarded Responses payload is account-neutral. A request carrying `previous_response_id` may cross only when its supplied input can be projected into a self-contained replay that retains prior output and fresh follow-up input. The projected request removes ChatGPT-owned response/item identifiers before source forwarding.

Requests containing uploaded `file_id` references, hosted/account-scoped input state, conversation-owned state, or an unprovable retained-response dependency remain on the existing fail-closed quota error path.

This rule treats the complete client-supplied replay as the new provider-neutral source of truth; it never asks the fallback provider to resolve a ChatGPT response identifier.

## API-key policy and accounting
Overflow uses the same API-key source-assignment policy as explicit Model Source routing. If a proxy API key is scoped to specific Model Sources, the designated fallback must be in that assignment set.

The reservation created for the subscription request is handed to the Model Source forwarding path rather than released and recreated. This preserves one reservation lifecycle and allows source usage/cost settlement to finalize the original reservation exactly once.

## Failure behavior
If no eligible fallback exists, the request is unsafe to replay, or the request belongs to a non-Responses/account-owned operation, codex-lb returns the existing subscription error unchanged. Once overflow dispatch begins, a fallback provider 4xx/429/5xx or transport failure is terminal for that request; codex-lb does not loop back into subscription account selection.

## Example
1. ChatGPT account A is quota-exhausted; account B still has quota. Normal account failover selects B and the Model Source is never contacted.
2. Later all eligible ChatGPT accounts report upstream quota exhaustion. A fresh Responses request is replay-safe, so codex-lb sends it to the designated source.
3. A request with a ChatGPT `previous_response_id` but only a new user message cannot prove a complete replay. It receives the normal `usage_limit_reached` response instead of silently losing conversation context.
4. After a ChatGPT account resets, the next fresh request uses normal subscription selection again.
