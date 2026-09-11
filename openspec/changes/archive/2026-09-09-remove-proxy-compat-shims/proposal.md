## Why

`app/modules/proxy/_support.py` and `app/modules/proxy/_warmup.py` were kept as
re-export-only compatibility shims after the proxy service decomposition so that
older private imports could migrate gradually. Every importer has since moved to
`app.modules.proxy._service.*`; the shims have zero importers in `app/`,
`tests/`, and `scripts/`, and the only remaining references are the architecture
checker asserting that they stay shims. Keeping dead shims plus a ratchet that
guards them is maintenance surface with no consumer.

## What Changes

- Remove the `_support.py` and `_warmup.py` compatibility shims.
- Retire the shim-only ratchet and the "service.py must not import shims" check
  from `scripts/check_proxy_architecture.py`; the remaining façade, package,
  threshold, and cross-domain checks are unchanged.
- Drop the shim sentence from the façade requirement; the façade itself and its
  required compatibility exports are unaffected.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-architecture`: the façade requirement no longer references compatibility
  shims because none exist.

## Impact

- Structural cleanup only. No runtime behavior, setting, schema, API, or
  `app.modules.proxy.service` façade export changes.
- The same PR also removes two unrelated never-imported modules
  (`app/core/clients/retry.py`, `app/core/resilience/retry_budget.py`) that no
  spec governs.
