## Why

An abandoned dashboard browser OAuth flow can keep codex-lb's localhost callback listener alive after the flow's 15-minute TTL. Separately, the documented Docker defaults publish host port 1455 for the entire time the container runs. Either condition can prevent Codex Desktop—which uses the same localhost callback port—from completing its own ChatGPT login, as reported in #2076.

## What Changes

- Make callback-listener lifetime follow the actual pending browser-flow lifetime, including autonomous cleanup when the last pending flow expires.
- Keep the shared listener alive while any unexpired browser flow still needs it, including overlapping flows with different deadlines.
- Ensure reset, completion, and replacement paths cannot leave an expiry task or callback listener orphaned.
- Stop publishing host port 1455 in the stock Docker and Compose paths; document device-code/manual callback account setup and an explicit dedicated-host opt-in.
- Add deterministic regression coverage for abandoned and overlapping browser OAuth flows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `replica-operations`: Strengthen the dashboard browser OAuth TTL contract so the originating replica releases its process-local callback listener when no unexpired browser flow remains, without waiting for another request or callback.
- `deployment-installation`: Keep host port 1455 free in default Docker deployments while preserving documented OAuth account-setup paths.

## Impact

- OAuth runtime lifecycle in `app/modules/oauth/service.py`.
- OAuth integration coverage in `tests/integration/test_oauth_flow.py`.
- Docker/Compose launch artifacts, deployment docs, and their unit-test contract.
- No API, database schema, setting, dependency, or dashboard-visible change.
