# Change: test-server continuous deployment

## Problem

Today the codex-lb repository builds Docker images (`Dockerfile` at the
repo root, multi-stage `python:3.14-slim` runtime) and publishes them to
`ghcr.io/andrewmalov/codex-lb` via `.github/workflows/release.yml` on the
`release: published` event. But **nothing deploys anywhere**:

- `.github/workflows/` has no job that talks to a remote host.
  No SSH, no self-hosted runner, no webhook, no `appleboy/scp-action`,
  no `kubectl apply`, nothing.
- `docker-compose.prod.yml` is single-service (`server`) and uses
  `build: { context: ., dockerfile: Dockerfile }`. Without an `image:`
  directive, `docker compose pull` cannot retrieve a registry image and
  the file is only useful for local re-builds.
- The string `193.149.18.210` does not appear in any file in the
  repository. There is no deployable test/staging target configured.

So when a developer pushes a change to `main`, the integration with a
**test server** they want to expose to operators and downstream QA is
manual: an operator has to grab the published image, SSH into the box,
and run a restart by hand. We want every push to `main` to deploy
automatically to the test server so QA sees latest `main` without
coordination.

## Solution

Introduce a continuous-deployment leg to GitHub Actions that, after CI
goes green on a push to `main`:

1. Builds the Docker image (already done by the existing `docker` job),
   and additionally **publishes** it to GHCR under the tags `:main` and
   `:sha-<sha>` so the deploy workflow has a stable, pull-able artefact.
2. SSHes to the configured test server (`root@193.149.18.210`) and
   runs `docker compose -f docker-compose.prod.yml pull server && up -d
   --remove-orphans` with the freshly pulled image. The container's
   healthcheck polls `/health/ready` until the proxy is ready (up to
   60 s) before the deploy job exits green.
3. Reuses the existing `docker-compose.prod.yml` after a minimal edit
   (replace the `build:` block with `image: ${CODEX_LB_IMAGE:-...}`)
   so `docker compose pull` becomes meaningful and the server-side
   `env_file: .env.local` continues to provide `CODEX_LB_DATABASE_URL`
   and friends — CI never sees those secrets.
4. Supports manual re-deploy via `workflow_dispatch` with an optional
   `image_tag` input so an operator can re-roll a known good SHA in one
   click without re-touching code.

The change is bounded to the test server only. Production rollout
(Helm + GHCR OCI chart that already exists) is unchanged.

## Changes

- Add a new job `publish-test-image` in `.github/workflows/ci.yml` that
  publishes the `:main` and `:sha-<sha>` tags to GHCR on every push to
  `main` (gated by `needs: docker` so Trivy still validates first).
- Modify `docker-compose.prod.yml` to use `image:` with an env-overridable
  default (`${CODEX_LB_IMAGE:-ghcr.io/andrewmalov/codex-lb:latest}`)
  so the deploy workflow can pull and re-create the container.
- Add a new workflow `.github/workflows/deploy-test.yml` triggered on
  `workflow_run` (CI succeeded on `main`) and on `workflow_dispatch`,
  which uses `appleboy/ssh-action@v1` to drive
  `docker compose pull && up -d` against `/srv/codex-lb/` on the test
  server.
- Add a requirement to the `deployment-installation` capability
  specifying that pushes to `main` MUST trigger a self-driven deploy to
  the configured test server.
- Document server-side one-time setup (Docker + compose plugin,
  `/srv/codex-lb/` directory layout, `authorized_keys` for the
  `TEST_SSH_KEY` deploy key, optional GHCR auth for a private package)
  in the change-level `context.md`.

## Out of scope

- Changing the existing release / versioned-image flow (release-please,
  `release.yml`, GHCR `:vX.Y.Z`, `:latest` for stable releases,
  `:vX.Y.Z-beta.N` for prereleases). That flow is orthogonal and keeps
  working.
- Production rollout (Helm chart at `oci://ghcr.io/andrewmalov/charts`
  is already published and is not affected).
- Moving from root SSH to a dedicated deploy user / `sudo NOPASSWD`
  sandbox — call it out as a follow-up if it becomes painful.
- Sealing off the workflow to a single deploy key per server;
  rotation/revocation procedures are not yet documented.
