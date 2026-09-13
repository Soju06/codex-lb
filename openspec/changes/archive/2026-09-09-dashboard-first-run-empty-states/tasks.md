## 1. Empty-state copy and CTA

- [x] 1.1 Add optional `action` slot to `EmptyState`
- [x] 1.2 Split Accounts/APIs first-run vs filtered-empty copy
- [x] 1.3 Split request-log first-run vs filtered-empty copy
- [x] 1.4 Add dashboard empty-account CTA to `/accounts` on cards and list
- [x] 1.5 Add `en` / `ko` / `zh-CN` keys for the new copy

## 2. Reports no-data and firewall deeplink

- [x] 2.1 Show no-data empty state on Reports line charts when `daily` is empty
- [x] 2.2 Redirect `/firewall` to `/settings?advanced=1#firewall`
- [x] 2.3 Expand Advanced from `advanced=1` or `#firewall` and set firewall `id`

## 3. Validation

- [x] 3.1 Unit/integration tests for empty copy, CTA, no-data charts, and `/firewall`
- [x] 3.2 `openspec validate --specs` for this change
- [x] 3.3 Browser-verify first-run empty surfaces and the firewall deeplink
