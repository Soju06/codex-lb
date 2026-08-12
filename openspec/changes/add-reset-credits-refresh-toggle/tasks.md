## 1. Settings and scheduler gate

- [x] 1.1 Add `rate_limit_reset_credits_refresh_enabled: bool = True` to `app/core/config/settings.py` next to the existing interval setting
- [x] 1.2 Add `enabled: bool = True` to `RateLimitResetCreditsRefreshScheduler` and make `start()` a no-op when disabled; wire the setting through `build_rate_limit_reset_credits_scheduler()`

## 2. Tests

- [x] 2.1 Unit-test that `start()` creates no task when disabled and creates the loop task when enabled
- [x] 2.2 Unit-test that the factory wires `rate_limit_reset_credits_refresh_enabled` from settings

## 3. Spec

- [x] 3.1 Update the `rate-limit-reset-credits` delta: scheduler starts with the lifespan when polling is enabled; settings expose the toggle with default `true`
