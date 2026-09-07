---
name: codex-lb-ha-deploy
description: Deploy application changes to codex-lb's already bootstrapped single-host HAProxy active-active Compose production environment using its temporary surge backend. Use automatically for deploy or redeploy requests targeting this Docker host; do not use for Helm/Kubernetes deployments or silently perform first-time HA bootstrap.
metadata:
  author: codex-lb
  version: "3.0.0"
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
   topology, rollout phase, all four backend states and weights, and eligible-backend count. Also
   inspect `git status --short` so the final report states that the image was built from a dirty
   working tree when applicable; do not modify or discard unrelated changes.
   Check resource headroom: this profile permits 12 GiB across four backends during rollout, with
   a 1-GiB native WebSocket queue budget inside each 3-GiB ceiling. Each candidate replica permits
   20 DB pool connections; four permit 80. Legacy replicas retain larger pools until replaced, so
   inspect actual PostgreSQL connection use and memory rather than treating these as current totals.
2. If `.codex-lb-ha/active-slot` is absent, report that this host is not bootstrapped. Explain that
   `./scripts/deploy-compose-ha.sh bootstrap` has a one-time public-port rebind interruption and wait
   for explicit acknowledgement before running it.
3. For an initialized topology, run `./scripts/deploy-compose-ha.sh deploy`. Wait until the command
   completes, including surge activation, all three bounded base-backend drains, and surge retirement.
   Changed HAProxy config is adopted by a validated graceful master-worker reload inside the script,
   never by recreating the public container. Old workers retain existing connections; drain waits
   use the full configured bound while old worker sessions are not visible in new-worker stats.
   Keep the user updated at least once per minute during a long build or drain. A legacy `blue` or
   `green` or `blue,green` topology marker is supported by the script and does not require HAProxy recreation.
4. On success, run `./scripts/deploy-compose-ha.sh status` and
   `curl --fail --silent http://127.0.0.1:2455/health/ready`. Confirm `Serving topology: blue,green,amber`,
   `Rollout phase: none`, blue, green and amber all have `UP` status with positive weights, exactly three
   steady-state backends are eligible, surge is stopped or ineligible at weight zero, and public
   readiness succeeds.
5. On failure, inspect HA status and scoped service logs. Report the failed stage and the slot still
   serving traffic. A later explicit deploy may resume a `retained`, `replacing`, `reloading` or `retiring` phase from the
   candidate image already built for that interrupted rollout; inspect the phase and intended
   revision before resuming because new source edits are not rebuilt mid-resume. Do not bypass
   prerequisite checks, recreate application services directly, or change HAProxy runtime state by
   hand; the script owns fail-closed recovery.

Run `./scripts/deploy-compose-ha.sh rollback` only when the operator explicitly asks and status
shows a healthy blue, green or amber backend in the `draining` phase. This cancels the visible drain and
aborts later replacements; it does not restore base backends already replaced. Rollback is not
available during a `retained`, `replacing`, `reloading` or `retiring` phase.
A legacy drain cancellation before amber exists records `retained:surge:blue,green,amber` and
keeps surge serving until a later explicit deploy completes that candidate without rebuilding it.
Never claim zero dropped long-lived connections: the configured drain deadline may terminate them.
For topology details, consult `docs/deployment/docker.md` and `openspec/specs/deployment-installation/`.
