## Why

Route probes exposed continuity validation gaps in Responses source pools. Normalized model fallback can erase known ownership, MCP approval references can bypass item ownership, and object-form conversation overrides can reach upstream without an ownership check. The overall review also reproduced file-pin bypass, unchecked hosted-tool references, rejected supported tool declarations, lost upstream observations, and a malformed candidate affecting valid peers.

## What Changes

- Preserve the original public-model scope while checking a normalized fallback source, without rejecting a continuation genuinely owned by that fallback scope.
- Check MCP approval response IDs against the owner of the returned approval request item.
- Check object-form conversation IDs introduced by source request overrides before dispatch.
- Reject effective input and hosted-tool file IDs before source admission or quota reservation, and isolate malformed input overrides to their source candidate.
- Bind code-interpreter containers and file-search vector stores to their published source, and admit declared stateless namespace and web-search options for direct source requests.
- Accept empty stream option objects as neutral direct-source controls and retain observed upstream metadata in JSON ownership-publication failure logs.
- Add route-level regressions for denial, valid continuation, and existing source and subscription compatibility.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: enforce reference ownership and candidate isolation while preserving valid single-source, pooled-tool and subscription behavior.

## Impact

Responses source selection and reference extraction on `/v1/responses` and `/backend-api/codex/responses`, their maintained integration tests, and the model-source-routing specification. No schema or operator setting changes are planned.
