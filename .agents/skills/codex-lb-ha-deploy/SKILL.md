---
name: codex-lb-ha-deploy
description: Deploy application changes to codex-lb's already bootstrapped single-host HAProxy blue/green Compose production environment. Use automatically for deploy or redeploy requests targeting this Docker host; do not use for Helm/Kubernetes deployments or silently perform first-time HA bootstrap.
metadata:
  author: codex-lb
  version: "1.0.0"
---

# codex-lb HA Deploy

Use the repository's tested HA script as the only deployment mutation path. Do not reproduce its
HAProxy runtime commands or replace the active application container directly.

## Authorization boundary

An explicit request to deploy or redeploy application changes authorizes one blue/green rollout,
the necessary read-only diagnostics, and post-deployment readiness checks. It does not authorize a
commit, push, database repair, manual HAProxy state change, or first-time HA bootstrap.

## Workflow

1. Work from the repository root. Run `./scripts/deploy-compose-ha.sh status` and record the active
   slot. Also inspect `git status --short` so the final report states that the image was built from a
   dirty working tree when applicable; do not modify or discard unrelated changes.
2. If `.codex-lb-ha/active-slot` is absent, report that this host is not bootstrapped. Explain that
   `./scripts/deploy-compose-ha.sh bootstrap` has a one-time public-port rebind interruption and wait
   for explicit acknowledgement before running it.
3. For an initialized topology, run `./scripts/deploy-compose-ha.sh deploy`. Wait until the command
   completes, including the bounded predecessor drain. Keep the user updated at least once per
   minute during a long build or drain.
4. On success, run `./scripts/deploy-compose-ha.sh status` and
   `curl --fail --silent http://127.0.0.1:2455/health/ready`. Confirm that the active slot changed,
   HAProxy and the new slot are healthy, and public readiness succeeds.
5. On failure, inspect HA status and scoped service logs. Report the failed stage and the slot still
   serving traffic. Do not bypass prerequisite checks or change HAProxy runtime state by hand; the
   script owns fail-closed recovery.

Run `./scripts/deploy-compose-ha.sh rollback` only when the operator explicitly asks and the status
shows a predecessor still draining. For topology details and bounded guarantees, consult
`docs/deployment/docker.md` and `openspec/specs/deployment-installation/`.
