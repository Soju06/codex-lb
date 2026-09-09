## 1. Settings surface

- [x] 1.1 Add `connect_address`, `additional_quota_registry_file`, and `forwarded_allow_ips` fields with tier comments; blank `CODEX_LB_*` values collapse to unset, `FORWARDED_ALLOW_IPS` keeps empty-string-means-trust-none.
- [x] 1.2 Give `forwarded_allow_ips` the `FORWARDED_ALLOW_IPS` / `CODEX_LB_FORWARDED_ALLOW_IPS` alias pair.
- [x] 1.3 Raise the settings ratchet 135 -> 138 with the promotion justification.

## 2. Consumers

- [x] 2.1 `TrustedProxyHeadersMiddleware` reads `get_settings().forwarded_allow_ips` (unset -> `127.0.0.1`).
- [x] 2.2 Connect-address resolver reads `get_settings().connect_address`.
- [x] 2.3 Additional quota registry loader reads `get_settings().additional_quota_registry_file`.

## 3. Documentation

- [x] 3.1 Generator renders alias env names and the process-level environment section; regenerate `docs/reference/settings.md`.

## 4. Verification

- [x] 4.1 Tests that set the promoted env names clear the settings cache; add unit coverage for the new fields.
- [x] 4.2 `make lint`, settings reference test, targeted unit/integration suites, `openspec validate`.
