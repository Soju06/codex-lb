# Routing Strategy Guide

The dashboard setting **Routing strategy** controls how eligible accounts are selected for each request. No strategy can guarantee account-safety outcomes; conservative use still depends on staying within OpenAI terms, using normal request volumes, and avoiding traffic patterns that would be unusual for your accounts.

For low-volume, policy-compliant personal use, start with **Capacity weighted** or **Relative availability** and keep sticky threads enabled. Those strategies preserve session locality while avoiding sudden all-traffic shifts to a single account.

| Routing strategy | Behavior | Trade-offs and recommended use |
|---|---|---|
| Capacity weighted | Prefers accounts with more usable quota headroom. | Good default for mixed pools and normal compliant usage. |
| Relative availability | Draws from the strongest available accounts with configurable weighting. | Smooths distribution while still preferring healthier accounts. |
| Usage weighted | Reacts to observed recent usage. | Useful when usage history should influence selection, but less direct than capacity-based routing. |
| Round robin | Cycles evenly through eligible accounts. | Simple and predictable, but ignores quota shape and reset timing. |
| Fill first | Uses one account heavily before moving on. | Best for controlled drain tests; less conservative for everyday traffic. |
| Sequential drain | Drains accounts in a fixed order. | Useful for maintenance or explicit account rotation, not a normal safety-first default. |
| Reset drain | Prioritizes capacity near reset windows. | Helps consume expiring quota, but can create timing-shaped bursts. |
| Single account | Pins all traffic to one selected active account. | Useful for isolation and debugging; no load balancing. |

Change the strategy live in the dashboard under **Settings → Routing** — no restart required.

## Reserve quota on individual accounts

On the **Accounts** page, an account can have an optional maximum-used
percentage. For example, a limit of `10%` reserves roughly 90% of that
account's standard quota for direct use.

When enabled, the limit is a hard routing gate for every strategy, including
sticky and single-account routing. Codex LB stops selecting the account once a
current standard quota window reports usage at or above the configured
percentage. It also preserves the account when current usage data is missing or
stale. The account's upstream status is not changed.

You can disable a configured limit without forgetting its percentage, or remove
it to clear the value. Because upstream usage is observed after requests finish,
delayed reporting and requests already in flight can move actual usage past the
displayed limit before the gate sees it.

---

*Spec: [account-routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/account-routing)*
