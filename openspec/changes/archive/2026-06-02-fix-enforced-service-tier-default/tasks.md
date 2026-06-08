## 1. Outbound Mapping

- [x] 1.1 Introduce `_UPSTREAM_OMIT_SERVICE_TIERS = frozenset({"auto", "default"})` in `app/modules/proxy/request_policy.py`.
- [x] 1.2 In `apply_api_key_enforcement`, when `enforced_service_tier` is in `_UPSTREAM_OMIT_SERVICE_TIERS`, set `payload.service_tier = None` instead of the literal value.
- [x] 1.3 Extend `api_key_service_tier_enforced` log line with `outbound_service_tier=` for operator visibility.

## 2. Tests

- [x] 2.1 Add `test_apply_api_key_enforcement_default_service_tier_omits_outbound_field` to `tests/unit/test_proxy_utils.py`.
- [x] 2.2 Add `test_apply_api_key_enforcement_auto_service_tier_omits_outbound_field`.
- [x] 2.3 Add `test_apply_api_key_enforcement_priority_service_tier_still_propagates` to lock the unchanged `flex`/`priority` path.

## 3. Spec Delta

- [x] 3.1 Add `openspec/changes/fix-enforced-service-tier-default/specs/api-keys/spec.md` describing the outbound omission contract.
- [x] 3.2 `openspec validate --specs` (if available).
