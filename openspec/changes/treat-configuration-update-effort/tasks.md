## 1. Spec deltas

- [x] `specs/responses-api-compat/spec.md`: restate the allowlist requirement
  with the configuration-update source and its precedence.
- [x] `specs/proxy-runtime-observability/spec.md`: add the request-log
  requirement for the effective effort.

## 2. Derivation and enforcement

- [x] `app/modules/proxy/request_policy.py`: read the last
  `configuration_update` input item, trim and lower-case its effort, and rank it
  above the model alias and the request-level field.
- [x] Keep the item untouched on the wire.
- [x] Validate the derived effort against `allowedReasoningEfforts` before
  quota reservation and dispatch.
- [x] Mirror the enforced effort into the recorded client effort when a policy
  fixes it.

## 3. Request-log call sites

- [x] Replace request-level reads with the shared log helper in the streaming,
  compact, and HTTP-bridge request-log writers.

## 4. Tests

- [x] Unit: derivation, precedence, blank and non-object items, allowlist
  rejection, log mapping for `ultra` and `minimal`, enforced-effort logging.
- [x] Integration: send a Responses request with a `configuration_update` item
  through the product path and assert the persisted `request_logs` row and the
  forwarded upstream effort.
- [x] Integration: assert `403 reasoning_effort_not_allowed` for a disallowed
  effort supplied only inside an item.

## 5. Verification

- [x] `uv run openspec validate treat-configuration-update-effort --strict`
- [x] `make lint`
- [x] `make typecheck`
- [x] `uv run pytest tests/unit -q`
- [x] `uv run pytest tests/integration -q`
