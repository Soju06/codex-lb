## Context

At the implementation baseline, `service.py` is 2,612 lines and
`load_balancer.py` is 3,187 lines, while the normative limits are 2,600 and
3,021. `LoadBalancer.select_account()` spans 478 lines and therefore remains
within its 527-line limit. The checker nevertheless passes because it carries
independently editable values of 2,617, 3,260, and 699.

The checker also enforces three existing structural ratchets that are not yet
listed in the normative requirement: 2,436 lines for the HTTP bridge mixin,
1,100 lines for the streaming mixin, and 1,200 lines for the largest
`ProxyService` method. Leaving those values in Python would retain a second
numeric policy source even after the three known mismatches were repaired.

The refactor is on routing-critical code, so file movement must preserve the
existing `service.py` and `load_balancer.py` import surfaces, test monkeypatch
seams, account filtering, additional-quota eligibility, and selection results.

## Goals / Non-Goals

**Goals:**

- Restore the normative file-size limits without raising or bypassing them.
- Make the OpenSpec requirement the sole owner of every numeric architecture
  ratchet enforced by the checker.
- Fail closed on an invalid threshold definition while continuing unrelated
  independently evaluable checks.
- Move only cohesive existing behavior and preserve runtime semantics and
  compatibility imports.

**Non-Goals:**

- Change routing policy, model eligibility, quota policy, account ownership,
  retry behavior, error codes, persistence, or lease settlement.
- Change any public API, wire schema, setting, database schema, deployment, or
  dashboard surface.
- Raise a ratchet, redesign `LoadBalancer.select_account()`, or perform
  unrelated proxy cleanup.

## Decisions

### Put all checker thresholds in one marked TOML block in the normative spec

The `Proxy architecture fitness gates are enforced` requirement will contain
one visible, machine-readable TOML block bounded by stable start/end markers.
It will list all six current limits. The checker will use Python's standard
`tomllib` module to load that block once per run and will pass the resulting
typed values directly to the limit checks.

The loader will require exactly one marked block, the exact expected key set,
and positive integers whose concrete type is `int` (so booleans are rejected).
Read, encoding, marker, TOML, key, and value errors become stable assertion
messages. When the block is invalid, only the six limit-dependent checks are
skipped; AST, façade, package, shim, and import-boundary checks still run.

Alternatives considered:

- Keep Python constants and add an equality regression. Rejected because two
  editable numeric copies would remain and could drift again.
- Parse numbers from natural-language Markdown. Rejected because line wrapping
  and harmless prose edits would make the contract brittle.
- Put a separate manifest beside `spec.md`. Rejected because keeping the
  machine-readable values visibly inside the normative requirement makes the
  ownership and review boundary explicit.

### Move lease-estimate helpers into the existing API-key usage domain

Move `_estimated_lease_tokens_from_request_usage_budget()` and
`_bounded_lease_token_estimate()` from `service.py` to
`_service/api_key_usage.py`, which already owns `ApiKeyRequestUsageBudget` and
reservation logic. `service.py` will re-export both names so HTTP bridge,
streaming, WebSocket, compact, test, and service-stub callers retain the same
façade lookup. This removes enough implementation from `service.py` to restore
the 2,600-line limit without creating a new module or import cycle.

### Extract model and additional-quota eligibility behind load-balancer wrappers

Create `_load_balancer/model_eligibility.py` for the existing self-free model
catalog filtering, catalog-omission provenance, additional-quota lookup, and
additional-quota eligibility helpers. Keep selection-input orchestration,
error-code construction, and `LoadBalancer._filter_accounts_for_additional_limit()`
in `load_balancer.py`.

The façade will re-export moved data carriers and helpers. The three helpers
whose tests or callers replace `load_balancer.get_model_registry` will remain
thin façade wrappers that pass the current façade registry factory into the
private implementation. This preserves both runtime lookup and existing
monkeypatch/import compatibility while removing roughly 190 net lines from the
public implementation module.

Alternatives considered:

- Move the roughly 655-line account-state projection domain. It is a strong
  longer-term boundary, but it changes many clock/monkeypatch seams and moves
  substantially more routing-critical code than this repair requires.
- Compact formatting or scatter small helpers across unrelated modules.
  Rejected because physical-line tricks and shallow file shaving do not create
  a durable ownership boundary.

### Verify behavior at existing public seams

The regression for SSOT binding will rewrite only the marked threshold block in
an inert fixture and prove the checker immediately follows it. Invalid-block
tests will cover missing/duplicate markers, malformed TOML, missing/unknown
keys, and invalid values. Existing stable-order, AST-degradation, clean-fixture,
and real-repository checks remain.

The moved runtime helpers will be covered through existing
`ProxyService`/service-stub and `LoadBalancer` entry points, including catalog
omission, service-tier filtering, additional-quota freshness/exhaustion, sticky
and unbound selection, and focused persistence/integration cases. Direct import
compatibility remains part of the architecture contract.

## Risks / Trade-offs

- **A spec formatting error could disable limit evaluation** -> fail closed with
  one explicit definition error and continue every unrelated check.
- **Moved model filtering could bind the wrong registry instance** -> keep
  façade wrappers that pass the live registry factory and retain monkeypatch
  regressions.
- **A mechanical move could change routing or quota results** -> keep function
  bodies unchanged and run focused unit plus realistic load-balancer integration
  coverage before publication.
- **Private modules grow while public files shrink** -> accept this deliberate
  trade-off because the move establishes focused ownership rather than hiding
  implementation in a generic helper module.

## Migration Plan

1. Add the checker regression in its red state and sync the complete modified
   requirement to the main proxy-architecture spec.
2. Implement strict TOML loading and switch all six limit checks to the loaded
   values.
3. Move the API-key lease-estimate and model-eligibility domains while
   preserving façade exports and wrappers.
4. Run scoped OpenSpec validation, architecture checks, focused unit and
   integration suites, lint, formatting, type checks, and independent review.

Rollback is a code-and-spec revert. There is no data, configuration, rollout,
or deployment migration.

## Open Questions

None.
