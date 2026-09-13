## Why

Codex clients configured with only the ordinary `codex-lb` provider cannot express a Trusted Access requirement before the first account-selection attempt. A later upstream `cyber_policy` result may arrive after `response.created`, when changing accounts or replaying the request is no longer safe.

## What Changes

- Publish a separate, explicitly selected Daybreak Blue Codex provider and profile that authenticates with a Codex LB API key and adds `X-Codex-LB-Required-Capability: trusted_cyber` to every provider request.
- Require a valid proxy API key for a direct Responses WebSocket request that carries the capability header even when global proxy API-key auth is disabled, without changing auth behavior for requests that omit the header.
- Authenticate and reject capability-bearing HTTP requests on provider-bound routing surfaces before account selection or upstream dispatch, including control, warmup, files, transcription, Images, reset-credit consume, and signed internal bridge calls; guard Chat Completions as a defensive equivalent sink.
- Authenticate and reject capability-bearing non-Responses WebSockets before owner lookup or upstream connection; keep authenticated model-catalog initialization available because it performs no account routing.
- Keep the existing ordinary `codex-lb` provider free of the capability carrier and preserve its current model and routing behavior.
- Select the existing `gpt-5.6-sol` model through the Daybreak profile; the profile name and authorized account surface, not a model alias alone, identify the intended use.
- Add inert regressions that load the published client configuration, exercise an installed Codex client in a network-isolated loopback harness, prove the Daybreak profile reaches capability ingress before first selection, and prove middleware aliases, signed bridge forwarding, and unsupported HTTP or WebSocket transports cannot reach ordinary routing.
- Document that the profile narrows routing only to accounts already marked and independently approved for security work; it does not grant Trusted Access.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Require a valid proxy API key through the existing `validate_proxy_api_key` dependency whenever the capability header is present, even when the global authentication switch is off.
- `responses-api-compat`: Define the opt-in Codex provider/profile contract that carries trusted-cyber intent before the first Responses WebSocket routing decision without changing ordinary client traffic.
- `images-api-compat`: Require capability-bearing Images requests to authenticate and fail closed before the existing ordinary image-routing pipeline.
- `chat-completions-compat`: Defensively reject the authenticated carrier before Chat Completions can enter ordinary routing.
- `realtime-api-compat`: Reject the carrier on call creation and non-Responses Live WebSockets before account or owner resolution.
- `proxy-admission-control`: Reject the carrier before opportunistic admission evaluates ordinary account capacity.
- `model-catalog-compat`: Keep authenticated local model-catalog initialization available without account routing.
- `proxy-warmup`: Reject the carrier before account-pool evaluation or fan-out.
- `files-upload-protocol`: Reject the carrier before reservation, account selection, upload registration, or polling.
- `audio-transcriptions-compat`: Reject the carrier before multipart parsing or transcription routing.
- `rate-limit-reset-credits`: Reject the carrier before reset-credit account or ChatGPT usage-identity routing while preserving authenticated local usage initialization.

## Impact

- Capability ingress guards, user-facing Codex client setup documentation, and checked-in inert configuration examples.
- Focused installed-client, direct Responses WebSocket, unsupported provider-route, Images, and Live WebSocket coverage at the client-config-to-capability-ingress seam.
- No proxy selector changes, new settings, environment variables, dependencies, migrations, dashboard changes, or automatic modification of user configuration. Ordinary requests keep their existing authentication and routing behavior.
