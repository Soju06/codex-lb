## 1. Settings and scheduler gate

- [x] 1.1 Add `rate_limit_reset_credits_refresh_enabled: bool = True` to `app/core/config/settings.py` next to the existing interval setting
- [x] 1.2 Add `enabled: bool = True` to `RateLimitResetCreditsRefreshScheduler` and make `start()` a no-op when disabled; wire the setting through `build_rate_limit_reset_credits_scheduler()`
- [x] 1.3 On disabled `start()`, read the persisted dashboard settings and log a configuration-conflict warning when `auto_redeem_reset_credits_before_expiry` is enabled (the refresh loop is the sole auto-redeem driver)

## 2. Tests

- [x] 2.1 Unit-test that `start()` creates no task when disabled and creates the loop task when enabled
- [x] 2.2 Unit-test that the factory wires `rate_limit_reset_credits_refresh_enabled` from settings
- [x] 2.3 Unit-test the disabled+auto-redeem conflict warning (warns when persisted opt-in is true, stays silent when false)
- [x] 2.4 Route-level integration tests: PUT rejecting a new auto-redeem opt-in while polling is disabled (`reset_credit_polling_disabled`), and a full PUT with an already-persisted opt-in still succeeding

## 3.5 Settings API guard

- [x] 3.5.1 Reject a new `auto_redeem_reset_credits_before_expiry` opt-in in `app/modules/settings/api.py` while polling is disabled; keep already-persisted opt-ins re-savable

## 3. Spec

- [x] 3.1 Update the `rate-limit-reset-credits` delta: scheduler starts with the lifespan when polling is enabled; settings expose the toggle with default `true`
