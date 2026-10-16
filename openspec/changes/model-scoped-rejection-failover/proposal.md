# Change: model-scoped-rejection-failover

## Why

An upstream `model_not_found` rejection is about the requested model, not the
health of the account that carried it. Treating it as a transient account
failure lets one unavailable model back off every candidate account and block
unrelated traffic. Conversely, a movable request can legitimately find an
account with a different entitlement before it has accepted an upstream
response. The WebSocket path must make that same distinction for connect and
pre-created terminal events without violating continuity ownership.

## What Changes

- Classify the exact upstream `model_not_found` code as model-scoped for both
  bounded pre-visible failover and account-health neutrality.
- Let a movable pre-created Responses WebSocket request retry that exact error
  before `response.created`; preserve the existing `invalid_request_error`
  behavior for all other ordinary invalid requests.
- Surface the original rejection for a required WebSocket owner; it must not
  exclude that owner and replace the error with an owner-unavailable result.

## Capabilities

### Modified Capabilities

- account-routing: model-scoped rejections do not alter account health.
- responses-api-compat: pre-created WebSocket model rejection is retryable
  only for movable requests and only before acceptance.

## Impact

One shared proxy classification, the existing stream health funnel, and the
existing Responses WebSocket connect and pre-created replay paths. No setting,
schema, dashboard, or transport protocol is added.
