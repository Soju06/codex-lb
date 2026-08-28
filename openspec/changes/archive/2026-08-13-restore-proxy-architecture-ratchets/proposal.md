## Why

The current `main` branch violates the proxy architecture ratchets that protect
the accepted `ProxyService` decomposition: the façade, load-balancer module, and
`LoadBalancer.select_account()` have all grown past their limits. CI stops at
the first violation, leaving the remaining debt hidden until each preceding
failure is fixed.

## What Changes

- Restore every current proxy architecture ratchet without increasing or
  bypassing any threshold.
- Port the façade extraction already started in PR #1416 into a replacement
  branch, and move sticky selection orchestration plus its policy helper out of
  `LoadBalancer.select_account()` into a focused private load-balancer package.
- Preserve account selection, affinity, failover, settlement, and external
  import behavior through characterization and regression coverage.
- Make the architecture checker report all independent violations in one run so
  a red baseline exposes its complete repair scope.
- Codify the proxy decomposition and ratchet contract in a main OpenSpec
  capability instead of leaving it only in an archived change and ADR.

## Capabilities

### New Capabilities

- `proxy-architecture`: Defines the stable proxy façade, private domain
  boundaries, account-selection decomposition, and CI-enforced architecture
  ratchets.

### Modified Capabilities

- None.

## Impact

- Affected code: `app/modules/proxy/service.py`,
  `app/modules/proxy/_service/support.py`,
  `app/modules/proxy/load_balancer.py`, a focused private account-selection
  module/package, and `scripts/check_proxy_architecture.py`.
- Affected tests: proxy façade/import compatibility, load-balancer selection
  characterization, architecture-check regressions, and the existing proxy
  integration suites.
- Public APIs, request/response schemas, routing policy, and database schemas do
  not change.
- The eventual combined PR supersedes #1416 after preserving its focused diff;
  closing the older PR remains a maintainer action.
