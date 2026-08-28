## Problem

API-key clients can still see the owner's upstream Codex quota state through `/v1/usage` and upstream quota headers on proxy responses. That exposes account-level usage and reset timing to temporary or third-party users who only need the quota on their own API key.

## Proposed change

Add a dashboard setting that hides upstream quota details from API-key-authenticated requests while leaving dashboard/admin views unchanged.

## Scope

- Add a dashboard setting for quota privacy.
- Omit upstream quota details from `/v1/usage` when the requester authenticated with an API key and the setting is enabled.
- Omit upstream quota headers from proxy responses for API-key-authenticated requests when the setting is enabled.
- Keep existing dashboard and owner-facing quota views unchanged.
