## Why

Auto transport always selects HTTP when an image-generation tool is present, but preparation still serializes the WebSocket payload for an unused size decision. This leaves that supported case incomplete under the existing active-consumer requirement.

## What Changes

- Skip the size serialization for auto image-generation requests and reuse the image-tool predicate for transport selection.
- Preserve explicit WebSocket size estimation and all active payload consumers.
- Add an explicit normative scenario and extend the real-origin preparation regression to verify zero unused encodes and identical HTTP body bytes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: Make the existing unconditional-HTTP preparation contract explicit for auto image-generation requests.

## Impact

The change touches Responses preparation in `app/core/clients/proxy.py`, its existing real-origin test, and the owning OpenSpec requirement and context. It adds no settings, APIs, dependencies, or transport behavior.
