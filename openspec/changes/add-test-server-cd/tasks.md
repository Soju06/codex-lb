# Tasks — Test server continuous deployment

## 1. CI: publish `:main` image on every push to `main`

- [x] 1.1 Add a new job `publish-test-image` to `.github/workflows/ci.yml`
      positioned after `docker` and before `ci-required`.
- [x] 1.2 Gate it with
      `if: github.event_name == 'push' && github.ref == 'refs/heads/main'`
      so PRs never trigger a registry push.
- [x] 1.3 Reuse the same SHA-pinned actions the existing `docker` job
      uses (`actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0`,
      `docker/setup-buildx-action@d7f5e7f509e45cec5c76c4d5afdd7de93d0b3df5`,
      `docker/build-push-action@f9f3042f7e2789586610d6e8b85c8f03e5195baf`).
- [x] 1.4 Login to GHCR with `secrets.GITHUB_TOKEN` and
      `username: ${{ github.actor }}`.
- [x] 1.5 Build with `push: true, load: false`,
      `tags: ghcr.io/${{ github.repository }}:main,
       ghcr.io/${{ github.repository }}:sha-${{ github.sha }}`,
      same `cache-from`/`cache-to` (`type=gha, scope=codex-lb-main`) as
      the existing `docker` job.
- [x] 1.6 Set `permissions: { contents: read, packages: write }` on the
      job; do NOT raise the workflow-level `permissions` block.
- [x] 1.7 Add `publish-test-image` to the `ci-required` job's
      `needs:` list so the aggregator job still treats it as a required
      gate.

## 2. `docker-compose.prod.yml`: switch from `build:` to `image:`

- [x] 2.1 Remove the `build: { context: ., dockerfile: Dockerfile }`
      block from the `server` service.
- [x] 2.2 Add
      `image: ${CODEX_LB_IMAGE:-ghcr.io/andrewmalov/codex-lb:latest}`
      so `docker compose pull` can resolve the image to fetch and so a
      fall-back invocation still has a sane default.
- [x] 2.3 Leave `env_file: .env.local`, `ports`, `volumes`,
      `healthcheck`, `restart`, `deploy.resources` exactly as they are.

## 3. New workflow: `.github/workflows/deploy-test.yml`

- [x] 3.1 Triggers:
      - `workflow_run`: `workflows: ["CI"], types: [completed],
        branches: [main]` (with inside-job filter
        `if: github.event.workflow_run.conclusion == 'success'`).
      - `workflow_dispatch` with an optional `image_tag` input,
        default `main`.
- [x] 3.2 Single job `deploy`, `runs-on: ubuntu-24.04`,
      `timeout-minutes: 10`, `permissions: { contents: read }`.
- [x] 3.3 `concurrency: { group: deploy-test, cancel-in-progress: false }`
      so a fresh push never kills a deploy that is mid-flight.
- [x] 3.4 Hardcode the deploy target in `env`:
      `DEPLOY_HOST=193.149.18.210`, `DEPLOY_USER=root`,
      `IMAGE_TAG=${{ inputs.image_tag || 'main' }}`,
      `IMAGE_REF=ghcr.io/${{ github.repository }}:${{ env.IMAGE_TAG }}`,
      `SERVER_DIR=/srv/codex-lb`. Use SSH key from `${{
      secrets.TEST_SSH_KEY }}`. All other deploy knobs (deploy key,
      allowlist, future host swap) live in repo secrets / settings, not
      in the workflow body.
- [x] 3.5 Use `appleboy/ssh-action@v1` with `command_timeout: 8m` and a
      single `script:` body that:
      1. `set -euo pipefail` and `cd "$SERVER_DIR"`.
      2. If `secrets.GHCR_USER` and `secrets.GHCR_PACKAGES_READ_TOKEN`
         are provided, log in to GHCR first
         (`echo "$GHCR_PACKAGES_READ_TOKEN" | docker login ghcr.io -u
         "$GHCR_USER" --password-stdin`).
      3. `CODEX_LB_IMAGE="$IMAGE_REF" docker compose -f
         docker-compose.prod.yml pull server`.
      4. `CODEX_LB_IMAGE="$IMAGE_REF" docker compose -f
         docker-compose.prod.yml up -d --remove-orphans server`.
      5. Poll `http://127.0.0.1:2455/health/ready` for up to 60 s.
      6. On failure, dump `docker compose ... logs --tail=200 server`
         to the job output and `exit 1`.

## 4. Documentation and OpenSpec delta

- [x] 4.1 Add the deployment requirement (see section 5 below) to a
      `specs/deployment-installation/spec.md` delta file under this
      change folder, using `## ADDED Requirements` (since this is a
      new capability surface, not a modification of an existing
      requirement).
- [x] 4.2 Add `context.md` describing server-side setup, secrets,
      rollback via `workflow_dispatch` with an explicit `image_tag`,
      and the GHCR-private vs GHCR-public trade-off.

## 5. Spec delta — `deployment-installation`

### NEW requirement (added to `openspec/specs/deployment-installation/spec.md` after sync)

> **Requirement: Test-server continuous deployment.**
> When a commit is pushed to `main` and `ci.yml` completes with
> success, codex-lb MUST deploy automatically to the configured test
> server. The deploy MUST pull the GHCR image tagged `:main` (which CI
> publishes on each push to `main` alongside `:sha-<sha>`) and recreate
> the `server` container via `docker compose -f docker-compose.prod.yml
> pull && up -d`. Server-side secrets (`CODEX_LB_DATABASE_URL` etc.)
> MUST stay in the server-local `.env.local`; CI MUST NOT receive or
> materialize them. Manual re-deploy via `workflow_dispatch` with an
> explicit `image_tag` MUST remain available so an operator can re-roll
> a known-good SHA in one click.

## 6. Server-side one-time setup (admin tasks, not committed code)

- [ ] 6.1 On `193.149.18.210`, install Docker Engine + the `docker
      compose` plugin if missing; confirm `docker compose version`
      succeeds.
- [ ] 6.2 Create `/srv/codex-lb/` containing the modified
      `docker-compose.prod.yml`, a populated `.env.local`, and an empty
      `codex-lb-data/` directory. The `codex-lb-data` named volume will
      be materialised on the first `up`.
- [ ] 6.3 Decide GHCR visibility. Either make the
      `ghcr.io/andrewmalov/codex-lb` package **public** so the server
      needs no auth, or keep it private and populate the
      `GHCR_USER` / `GHCR_PACKAGES_READ_TOKEN` repo secrets so the
      deploy script can `docker login` first. Document the choice in
      `context.md`.
- [ ] 6.4 Append the public counterpart of `TEST_SSH_KEY` to
      `~/.ssh/authorized_keys` for the user the workflow logs in as.
      For now this is `root`; a follow-up change may introduce a
      dedicated `deploy` user with `sudo NOPASSWD: /usr/bin/docker
      compose ...`.
- [ ] 6.5 Allow inbound SSH (TCP/22) from the GitHub Actions IP ranges
      (fetched from `https://api.github.com/meta` → `actions`) or front
      the server with a tunnel (Tailscale, Cloudflare Tunnel) that
      CI can reach.

## 7. Local gates

- [x] 7.1 `make lint` (ruff) stays green with the workflow + compose
      edits. (Workflows and compose are not Python, but ruff is the
      project's lint uniform; this catches accidental Python typos in
      any helper scripts.)
- [x] 7.2 `openspec validate add-test-server-cd --strict` reports
      `is valid` before merge.

## 8. After merge to `main` (verification + archive)

- [ ] 8.1 Push a no-op commit to `main` and confirm GHCR has a fresh
      `:main` + `:sha-<sha>` tag, the `Deploy test` workflow runs once,
      and `curl http://193.149.18.210:2455/health/ready` returns 200
      from outside the server.
- [ ] 8.2 Force a CI failure (e.g. `RUN exit 1` in `Dockerfile`)
      and confirm `Deploy test` does NOT run.
- [ ] 8.3 Run `Actions → Deploy test → Run workflow → image_tag=sha-…
      <older>` and confirm rollback lands on the chosen SHA.
- [ ] 8.4 `openspec sync add-test-server-cd` to merge the delta into
      `openspec/specs/deployment-installation/spec.md`, then
      `openspec archive add-test-server-cd --yes` once the change is
      shipped and verified.
