## Why

Remote first-run dashboard access currently requires `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` before an admin can set the initial password. Some deployments need to disable that remote bootstrap gate entirely and allow the dashboard to behave like local access during first-run setup.

## What Changes

- Add `CODEX_LB_DISABLE_BOOTSTRAP_TOKEN` as a boolean environment flag.
- When disabled, remote dashboard session checks and password setup bypass the bootstrap-token requirement.
- Keep the existing bootstrap-token flow unchanged when the flag is not enabled.

## Capabilities

### Modified Capabilities

- `admin-auth`

## Impact

- Code: `app/core/config/settings.py`, `app/core/auth/dependencies.py`, `app/modules/dashboard_auth/api.py`
- Deployment: `.env.example`, `deploy/helm/codex-lb/templates/configmap.yaml`, `deploy/helm/codex-lb/values.yaml`
- Tests: dashboard auth middleware coverage, settings env parsing
