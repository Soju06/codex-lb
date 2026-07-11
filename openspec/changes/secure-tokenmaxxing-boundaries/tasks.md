## 1. Specification

- [x] 1.1 Add admin-auth, fleet-summary, and request-log-retention deltas.

## 2. Cloudflare Access authentication

- [x] 2.1 Add validated Access JWT configuration and fail-closed validation.
- [x] 2.2 Derive the trusted dashboard actor only from the validated email claim.
- [x] 2.3 Prove missing, forged, expired, wrong issuer, wrong audience, wrong
      domain, and JWKS failure are rejected.

## 3. Fleet capacity

- [x] 3.1 Extend fleet summary with generated time, included/excluded accounts,
      five-hour and weekly used percentages, stale state, and additional quota.
- [x] 3.2 Prove clamping, rounding, exhaustion, exclusion, and stale suppression.

## 4. Retention

- [x] 4.1 Add scheduled request-log hard deletion with configurable retention.
- [x] 4.2 Prove expired logs are deleted and recent logs are retained.
- [x] 4.3 Document and verify production payload/archive settings remain off.

## 5. Delivery

- [ ] 5.1 Run targeted and aggregate tests.
- [ ] 5.2 Run fresh review and security re-audit.
- [ ] 5.3 Deploy, click-test, exercise API-key create/use/revoke, and observe.
