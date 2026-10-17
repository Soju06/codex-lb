## Why

A paused account still receives new response.create frames on an already open direct WebSocket. Account selection and retained reconnect checks do not guard ordinary socket reuse.

## What Changes

- Check cluster-coherent account availability at the final response-create send boundary, after admission waits.
- Reject only the unsent request, release its reservations and log the failure, preserving the live socket, existing response and ownership anchors.
- Cover pause, reactivation, account-bound deltas, concurrent responses and late pause during admission with integration tests.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: enforce account availability for each direct WebSocket response-create dispatch.

## Impact

Direct WebSocket sender and integration tests. No settings, schema, credentials, provider payload or account allocation policy changes.
