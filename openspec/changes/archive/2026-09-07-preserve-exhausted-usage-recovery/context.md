# Exhausted usage during early recovery

This repair follows the [account-routing cooldown requirement](specs/account-routing/spec.md) and the existing [usage recovery policy](../../../specs/usage-refresh-policy/spec.md). It addresses a reproduced full-CI blocker while integrating current main into the HTTP continuation-owner work.

A newer sample does not necessarily prove recovery. For example, an upstream 429 is followed by a fresh primary sample reporting 100% with 30 minutes remaining. Once the observing replica's short runtime cooldown expires, that sample must not clear its still-running upstream block and cause another attempt. The existing HTTP test verifies the refresh write, cache invalidation, and pool-exhaustion response together.

The condition uses normalized window usage. Raw expired samples may still read 100%, while their derived usage is zero. Active accounts continue to treat snapshots as advisory; credit handling and cross-replica cooldown enforcement retain their existing behavior. There is no setting or migration.
