# Recovering a stale account hold

Use **Force probe** on the Accounts page when an account remains limited even though it may be usable again. The action sends one small upstream request and refreshes usage. It does not run periodically or redeem reset credits.

For a hold recorded by this version, the default probe uses the rejected model and service tier. If that request completes successfully, codex-lb clears the unchanged hold and refreshes routing state. An explicit probe of another model does not clear it. Existing sessions, file ownership and routing policy remain in place.

The probe response distinguishes three facts:

| Field | Meaning |
| --- | --- |
| `probeStatusCode` | Observed upstream HTTP status; `0` means no response headers arrived. |
| `probeCompleted` | A valid completed response was received. HTTP 200 alone is insufficient. |
| `holdRecovered` | The guarded update cleared the exact hold observed before the probe. |

A timeout, incomplete response, failed request, expired probe claim, newer rejection or changed credentials prevents this recovery. A competing probe receives HTTP 409. Ordinary usage and deadline recovery still apply independently.

Historical holds have no trustworthy rejected scope and are not cleared by this new path. A successful probe does not invent that missing history. Upgrade every rejection writer before relying on generation-based recovery across replicas; mixed old and new writers cannot provide that guarantee.

For example, an Astra/default hold can be cleared by a completed Astra/default probe. A Spark probe or a positive pooled usage percentage cannot establish that the Astra hold is stale.

The source of truth is [account routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/account-routing) and [usage refresh policy](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy).
