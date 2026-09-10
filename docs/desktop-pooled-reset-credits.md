# Codex Desktop pooled reset credits

Show available reset credits across imported accounts and use the one expiring soonest from Desktop's normal Reset action. One click spends one credit and resets its owning account. It does not reset every account in the pool.

Source of truth: [Desktop pooled reset credits specification](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/desktop-pooled-reset-credits).

## Enable

Set up the [Desktop pooled usage relay](desktop-pooled-usage.md) with a server version containing the reset adapter. Keep your original ChatGPT login and import that account into LB. Finish any pending reset attempt before enabling the new policy.

In Settings, under Reset Credits, enable **Pool resets in Codex Desktop**. This setting defaults off and requires dashboard write access. It authorizes eligible imported ChatGPT identities to use credits across the imported pool. The separate automatic expiry redemption setting is independent.

Open Desktop's reset dialog to load the combined count and genuine credits. A default Reset uses the earliest known expiry. Selecting a credit card uses that credit. Credits without an expiry appear after dated credits. Paused, deactivated, reauthentication-required, pending-deletion and expired contributions are excluded.

If a selected owner has nothing to reset, Desktop shows that upstream result. The action does not silently try another credit. A network failure keeps the request bound to the same owner and credit on retry.

## Limits

The list needs complete fresh account observations. If an account cannot refresh, the reset list reports unavailable and the usage response omits its optional reset summary. Missing data never creates credits or a successful reset.

The relay routes the native HTTP reset dialog. The separate app-server reset tool remains scoped to the signed-in account. Account identity, plan, billing and other balances retain their original ownership.

Local tests verify routes, selection, concurrency, restart retries and migration behavior using synthetic credits. Request formats were inspected in Desktop 26.903.61454 with CLI 0.153.4. No real reset was consumed during verification; live pooled-reset acceptance remains outstanding.

## Disable and recovery

Turn off **Pool resets in Codex Desktop** to return inventory and new consumption to the signed-in account. Existing cross-account retries fail without switching credits. Keep the reset adapter and its database ledger installed after reset attempts have been accepted.

Downgrade refuses to remove a nonempty redemption ledger. Do not bypass the adapter with an older relay or erase its ledger: a pending default request must retain its original selection. Repeat compatibility checks after Desktop updates.
