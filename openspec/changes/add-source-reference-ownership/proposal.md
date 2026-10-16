## Why

Native source sessions and HTTP continuation must agree on the source credential owning response, item and tool references. This prerequisite makes that ownership durable before delivery.

## What Changes

- Record source references and credential revisions with durable history.
- Select equivalent authorized source credentials with bounded retries for source-neutral requests.
- Keep reference-bound requests on their owner and fail closed after owner removal or credential changes.

## Capabilities

### Modified Capabilities

- `model-source-routing`: source selection, durable ownership and safe retry.

## Impact

Source dispatch and retention, two additive migrations, routing regression tests. No subscription-overflow or instance setting.
