# Desktop pooled reset credits context

[Contract](spec.md). [Setup and limits](../../../docs/desktop-pooled-reset-credits.md).

## Ownership and selection

The imported account pool can contain credits that expire before the signed-in account's credits. Enabling the dashboard policy lets an authenticated eligible imported ChatGPT caller list and redeem across that pool. Billing and subscriptions remain owned by the original caller. An LB API key does not authorize this capability.

For example, if account A has a credit expiring next week and account B has one expiring tomorrow, the default action consumes B's credit using B's credentials. One credit resets B only. Explicit card selection uses the selected credit. Unknown expiries sort last; equal expiries use owner and credit ID order. An upstream rejection ends that action without trying another owner.

## Inventory and retries

The list requires complete observations at most 180 seconds old. Missing snapshots refresh with at most four concurrent workers and a ten-second aggregate deadline. Each worker owns its database session. The usage response reads this same cache without waiting for reset refresh and omits the optional reset field when the cache is incomplete. A new consume forces a refresh before selecting.

Before upstream mutation, a durable ledger binds the original upstream caller ID and native request ID to one local owner, its upstream ChatGPT identity and genuine credit ID. Reauthorization of the local row cannot redirect a pinned retry. The ledger survives account deletion. Atomic first-writer selection handles simultaneous requests; the existing account redemption serializer protects the selected credit across replicas. The service rechecks caller, owner and policy at mutation admission.

Original-account redemption preserves the native upstream request ID, including retries that began before installing the adapter. Cross-account redemption namespaces the key by caller so two callers cannot share an upstream attempt. Neither policy changes nor process restarts change the selected owner. A retry whose owner is gone fails. Disabling pooling retains the ledger and blocks cross-account retries.

## Client compatibility and recovery

Read-only inspection of Codex Desktop 26.903.61454 with bundled CLI 0.153.4 found GET `/wham/rate-limit-reset-credits` and POST `/wham/rate-limit-reset-credits/consume`. The latter carries optional `credit_id` and stable `redeem_request_id`. The native controller retains both on transport failure and distinguishes reset, already redeemed, no credit and nothing to reset. The separate app-server reset RPC used by native tools remains original-account scoped.

Finish any pending reset attempt in the existing Desktop session before first enabling pooling. A request sent directly to OpenAI before this adapter existed has no local owner binding. The default-off migration preserves the original upstream idempotency key until opt-in.

Disable the dashboard policy to stop pool redemption. Keep the adapter and ledger installed after accepting reset attempts. Downgrade refuses to drop a nonempty ledger; bypassing it with an older relay can reinterpret a pending default action on the original account. No ledger TTL or cascade deletion is safe without an upstream retry-expiration contract.

Verification uses synthetic upstream responses and isolated databases. No real reset credit was consumed, and the native pooled-reset feature has not been deployed or accepted in a live Desktop session. Repeat request-contract checks after Desktop updates.
