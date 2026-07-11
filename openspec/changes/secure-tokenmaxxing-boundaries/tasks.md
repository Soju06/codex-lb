## 1. Specification

- [x] 1.1 Add admin-auth, fleet-summary, and request-log-retention deltas.

## 2. Cloudflare Access authentication

- [x] 2.1 Add validated Access JWT configuration and fail-closed validation.
- [x] 2.2 Derive the trusted dashboard actor only from the validated email claim.
- [x] 2.3 Prove missing, forged, expired, wrong issuer, wrong audience, wrong
      domain, and JWKS failure are rejected.
- [x] 2.4 Exempt health and read-only internal probes from required Access JWT
      while keeping dashboard fallback authentication and mutating internal
      endpoints fail-closed without blocking API-key fleet/proxy traffic.

## 3. Fleet capacity

- [x] 3.1 Extend fleet summary with generated time, included/excluded accounts,
      five-hour and weekly used percentages, stale state, and additional quota.
- [x] 3.2 Prove clamping, rounding, exhaustion, exclusion, and stale suppression.
- [x] 3.3 Clamp each account's remaining percentage before averaging fleet
      utilization.

## 4. Retention

- [x] 4.1 Add scheduled request-log hard deletion with configurable retention.
- [x] 4.2 Prove expired logs are deleted, recent logs are retained, and bulk
      retention deletes do not synchronize expired ORM row identities.
- [x] 4.3 Document and verify production payload/archive settings remain off.
- [x] 4.4 Keep readiness stable across the cleanup interval while failing on
      unhealthy cleanup or rows outside the grace window.
- [x] 4.5 Run the Onda compose deployment through the `codex-lb` entry point so
      runtime log configuration is honored.
- [x] 4.6 Move behavior contracts and review artifacts from `docs/` into this
      OpenSpec change context.

## 5. Delivery

- [ ] 5.1 Run targeted and aggregate tests.
- [ ] 5.2 Run fresh review and security re-audit.
- [ ] 5.3 Deploy, click-test, exercise API-key create/use/revoke, and observe.
