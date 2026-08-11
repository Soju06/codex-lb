## 1. Default-on guardian

- [x] 1.1 Flip `auth_guardian_enabled` default to `True` in
      `app/core/config/settings.py`.
- [x] 1.2 Update `docs/reference/settings.md` default column for
      `CODEX_LB_AUTH_GUARDIAN_ENABLED`.
- [x] 1.3 Update `tests/unit/test_settings_multi_replica.py` default
      assertion and add a build-path test asserting the scheduler is enabled
      with default settings on a single replica.

## 2. Paused account coverage

- [x] 2.1 Admit `paused` accounts in the guardian staleness predicate in
      `app/core/auth/guardian.py`; keep `reauth_required`/`deactivated`
      excluded.
- [x] 2.2 Unit tests: paused+stale account is selected as a candidate and
      refreshed; fresh paused account is skipped; `reauth_required` and
      `deactivated` accounts are never selected; paused account status is
      unchanged after successful refresh.

## 3. Spec sync

- [ ] 3.1 Apply the delta to `openspec/specs/usage-refresh-policy/spec.md`
      when archiving.
