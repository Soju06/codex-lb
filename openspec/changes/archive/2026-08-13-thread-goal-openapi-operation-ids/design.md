## Context

FastAPI generates one route-level `unique_id` for an `APIRoute`. The thread-goal compatibility handler is currently registered once with `methods=["GET", "POST"]`, so OpenAPI generation reuses that single identifier for both operations. The generated suffix also depends on which method FastAPI reads first from an unordered method set, making the duplicate identifier process-dependent.

The route must retain both methods, its exact path, the same handler body, router dependencies, request forwarding, and response behavior. The externally affected seam is the unauthenticated OpenAPI document; the existing method-parameterized integration test covers runtime parity.

## Goals / Non-Goals

**Goals:**

- Make every generated OpenAPI `operationId` unique.
- Make the thread-goal GET and POST identifiers deterministic without departing from FastAPI's default naming convention.
- Preserve GET and POST runtime forwarding through one handler.
- Lock both the global schema invariant and the exact target identifiers with a real `/openapi.json` regression.

**Non-Goals:**

- No split into separate handler functions.
- No explicit `operation_id=` override or repository-wide operation naming convention.
- No route alias, path, dependency, authentication, request, response, or upstream behavior change.
- No global route refactor, frontend change, or unrelated OpenAPI cleanup.

## Decisions

- Register the existing handler with adjacent `@router.get(...)` and `@router.post(...)` decorators. Each decorator creates a single-method `APIRoute`, so FastAPI's normal generator produces deterministic `_get` and `_post` suffixes while both routes retain the same handler identity. The rejected alternative is an explicit `operation_id=` override, which would establish a one-off naming convention and mask the route-registration cause.
- Test the public schema by requesting unauthenticated `GET /openapi.json`, enumerating documented HTTP operations, and asserting global `operationId` uniqueness plus the two exact thread-goal identifiers. The rejected alternative is inspecting router internals, which would not verify the emitted client contract.
- Keep the existing parameterized GET/POST forwarding test unchanged. This separates the new schema contract from existing runtime compatibility evidence.

## Risks / Trade-offs

- **Risk: decorator order could make route registration hard to read.** → Keep both decorators adjacent on the existing function and use the same path literal.
- **Risk: another route may later introduce a duplicate identifier.** → The full-schema uniqueness assertion fails at the public OpenAPI seam, not only for the current pair.
- **Trade-off: exact generated identifiers couple the regression to FastAPI's established default naming convention.** → This is intentional because same-source deterministic identifiers are the compatibility contract being fixed, and no explicit repository naming convention exists.

## Migration Plan

No data or operator migration is required. Deployment changes only generated OpenAPI metadata; rollback restores the prior route registration and duplicate schema behavior.

## Open Questions

None.
