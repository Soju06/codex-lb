## Overview

Apply parameter sanitization at the shared Responses payload normalization layer so all call paths (`/backend-api/codex/responses`, `/v1/responses`, `/responses/compact`, and Chat->Responses mapping) inherit the same behavior.

## Design

1. Extend `_UNSUPPORTED_UPSTREAM_FIELDS` in `app/core/openai/requests.py` to include:
   - `prompt_cache_retention`
   - `temperature`
2. Keep sanitization in `ResponsesRequest.to_payload()` and `ResponsesCompactRequest.to_payload()` as the single source of truth.
3. Preserve unknown extra fields that are not explicitly listed as unsupported.

## Testing Strategy

- Unit: verify `ResponsesRequest.to_payload()` drops `prompt_cache_retention` and `temperature` while preserving unrelated extra fields.
- Unit: verify `ResponsesCompactRequest.to_payload()` also drops these fields.
- Unit: verify Chat request mapping path no longer forwards `temperature` once converted to Responses payload.

## Risks and Mitigations

- Risk: accidentally dropping too many fields and breaking forward compatibility.
  - Mitigation: keep a narrow allow-to-drop list and assert non-listed fields remain in payload.
