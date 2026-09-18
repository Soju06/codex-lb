## Why

The HTTP owner-readiness change omits valid response IDs first exposed by `response.queued` or `response.in_progress`. A background JSON acknowledgement or in-progress SSE event can therefore leave an immediate continuation dependent on detached request-log persistence.

## What Changes

- Include queued and in-progress lifecycle responses in early ownership publication, including the later-event relay path.
- Extend the existing real-route regression to cover background JSON acknowledgements and an in-progress event after token delivery.
- Preserve account and caller scope, provenance filtering, detached persistence, and provider control over unfinished-response usability.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: explicitly include queued and in-progress lifecycle IDs in the existing HTTP owner-readiness requirement.

## Impact

Shared lifecycle classification, HTTP stream relay classification, the existing owner regression, and the owning specification. No new setting, schema, dependency, or upstream availability guarantee.
