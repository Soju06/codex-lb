## 1. Implementation

- [x] 1.1 Thread requested secondary availability as a distinct priority signal set.
- [x] 1.2 Keep requested-limit selection from treating missing requested secondary
  usage as ordinary secondary usage.
- [x] 1.3 Ensure `cooldown_until` is enforced regardless of quota-bypass flags.
- [x] 1.4 Keep requested-limit status recovery behavior unchanged for existing
  account status/bypass semantics.

## 2. Verification

- [x] 2.1 Update/add unit tests for `select_account` and requested-limit ranking.
- [x] 2.2 Update/add unit tests for quota-bypass/cooldown interaction.
- [x] 2.3 Run focused backend tests for proxy balancer and usage-repository paths.
