---
name: codex-lb-ha-deploy
description: Deploy application changes to codex-lb's already bootstrapped single-host HAProxy active-active Compose production environment using its temporary surge backend. Use automatically for deploy or redeploy requests targeting this Docker host; do not use for Helm/Kubernetes deployments or silently perform first-time HA bootstrap.
metadata:
  author: codex-lb
  version: "2.0.0"
---

# codex-lb HA Deploy

Use the repository's tested HA script as the only deployment mutation path. Do not reproduce its
HAProxy runtime commands or replace the active application container directly.

## Authorization boundary

An explicit request to deploy or redeploy application changes authorizes one active-active surge
rollout, the necessary read-only diagnostics, and post-deployment readiness checks. It does not
authorize a commit, push, database repair, manual HAProxy state change, rollback, or first-time HA
bootstrap.

## Workflow

1. Work from the repository root. Run `./scripts/deploy-compose-ha.sh status` and record the serving
   topology, rollout phase, all three backend states and weights, and eligible-backend count. Also
   inspect `git status --short` so the final report states that the image was built from a dirty
   working tree when applicable; do not modify or discard unrelated changes.
2. If `.codex-lb-ha/active-slot` is absent, report that this host is not bootstrapped. Explain that
   `./scripts/deploy-compose-ha.sh bootstrap` has a one-time public-port rebind interruption and wait
   for explicit acknowledgement before running it.
3. For an initialized topology, run `./scripts/deploy-compose-ha.sh deploy`. Wait until the command
   completes, including surge activation, both bounded base-backend drains, and surge retirement.
   Keep the user updated at least once per minute during a long build or drain. A legacy `blue` or
   `green` topology marker is supported by the script and does not require HAProxy recreation.
4. On success, run `./scripts/deploy-compose-ha.sh status` and
   `curl --fail --silent http://127.0.0.1:2455/health/ready`. Confirm `Serving topology: blue,green`,
   `Rollout phase: none`, blue and green both have `UP` status with positive weights, exactly two
   steady-state backends are eligible, surge is stopped or ineligible at weight zero, and public
   readiness succeeds.
5. On failure, inspect HA status and scoped service logs. Report the failed stage and the slot still
   serving traffic. A later explicit deploy may resume a `replacing` or `retiring` phase from the
   candidate image already built for that interrupted rollout; inspect the phase and intended
   revision before resuming because new source edits are not rebuilt mid-resume. Do not bypass
   prerequisite checks, recreate application services directly, or change HAProxy runtime state by
   hand; the script owns fail-closed recovery.

Run `./scripts/deploy-compose-ha.sh rollback` only when the operator explicitly asks and status
shows a healthy blue or green backend in the `draining` phase. This cancels the visible drain and
aborts later replacements; it does not restore base backends already replaced. Rollback is not
available during a `replacing` or `retiring` phase. For topology details and bounded guarantees,
consult `docs/deployment/docker.md` and `openspec/specs/deployment-installation/`.
