# Persist dashboard OAuth flow state for multi-replica

## Why

The dashboard OAuth add-account / reauth flow keeps its per-flow state (PKCE
`code_verifier`, `state` token, device-code poll metadata, status) in a
process-local in-memory dict (`_OAUTH_STORE` in `app/modules/oauth/service.py`).
With N replicas behind a load balancer, the browser callback, the manually
pasted callback URL, or the device-code status poll can land on a different
replica than the one that started the flow. That replica has no verifier or
state, so the authorization-code exchange fails ("state mismatch") and status
polls report `pending` forever even after another replica succeeded. Adding or
re-authenticating an account is therefore intermittently broken on every
multi-replica deployment.

## What Changes

- Add an `oauth_flow_states` table (Alembic migration on the current head)
  keyed by `flow_id`, storing the encrypted PKCE verifier, `state` token,
  method, status, device-code metadata, intended account id, and timestamps.
- Persist every flow to the shared DB at creation and write status transitions
  (success/error) durably, so any replica can complete a flow it did not start.
- Read flow state (by `flow_id` and by `state` token) and the authoritative
  status from the DB on the callback, manual-callback, device-complete, and
  status paths, hydrating the process-local runtime as needed.
- Encrypt the PKCE verifier at rest with the existing `TokenEncryptor`.
- Expire abandoned flows via a short TTL: filter expired pending flows on read
  and purge them (plus bound retained terminal rows) opportunistically on write.
- Keep inherently process-local runtime (the localhost callback server and the
  in-process device poll task) local, keyed by `flow_id`.
