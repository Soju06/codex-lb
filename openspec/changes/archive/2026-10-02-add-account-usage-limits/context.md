# Context: per-account usage limits

The stable rationale (observation-bound limits, fail-closed data, owner authorization, and selection fences) lives in [account-routing context](../../../specs/account-routing/context.md#account-usage-limits). This note records the decisions specific to this change.

## Policy model

One enabled flag controls a default maximum-used percentage and optional 5-hour and weekly overrides. An override matches by normalized window duration, so monthly and nonstandard windows use the default only. A missing override inherits the default; without a default, unmatched windows are unrestricted. Older single-threshold policies keep their behavior because the default is the original scalar field.

With 5-hour and weekly usage of 65% and 75% and caps of 70% and 90%, both windows are available. At 72% 5-hour usage the account is blocked.

## Reserve editor

The API stores maximum-used percentages; the editor shows the reserve (100 − cap) that applies to each window rather than the stored fields:

| Account windows | Rows |
| --- | --- |
| 5-hour and weekly | 5-hour, Weekly |
| Weekly only | Weekly |
| Monthly only | Monthly (default) |
| Unknown, or only nonstandard | All windows (default) |
| 5-hour with monthly or nonstandard | 5-hour, Other windows (default) |

On save, a standard row equal to the default it would inherit clears its override, and any other value becomes an override. A blank row means no reserve, which needs an explicit full-use override (maximum used 100) when a default exists. Without a default row, the stored default is not sent and is retained. Clearing every row removes the policy, including overrides for windows the account no longer reports, because such an override would fail closed.

Example: default 80, 5-hour 70, weekly 90 (reserves 20, 30, and 10). The editor shows 5-hour 30 and Weekly 10. Changing Weekly to 15 sends `{enabled: true, percent5H: 70, percentWeekly: 85}`.

The preview computes usable quota as provider remaining minus the draft reserve, floored at zero, and marks a window reached when remaining ≤ reserve, which mirrors the admission rule `used ≥ cap`. A disabled policy shows all remaining quota as usable.

Known trade-off: keeping an untouched window's reserve while the default changes needs an explicit override for that window. An enabled override requires a current matching observation, so if that window's last observation expired, the account reports `data_unavailable` until a fresh observation arrives, even though the default alone would have treated the window as reset. This arises only when a default row is shown beside a standard window, and it fails closed, so the editor accepts it rather than silently changing the reserve a row shows.

## Migrations

`20260728_010000_add_account_usage_limits` adds the scalar policy and `20260910_010000_add_usage_limit_overrides` adds the overrides. The merge revisions `20261002_000000_merge_usage_limits_and_main_heads` and `20261002_010000_merge_usage_limit_review_heads` join them with upstream history without changing any published identifier or parent. Downgrading the override revision disables policies that have no scalar threshold before dropping the override columns. Upgrade tests start from each published lineage and check that saved policies survive.
