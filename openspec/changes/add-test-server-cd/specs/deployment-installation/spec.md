# deployment-installation — Test server continuous deployment (delta)

## ADDED Requirements

### Requirement: Test server continuous deployment

When a commit is pushed to `main` and `.github/workflows/ci.yml` completes with conclusion `success`, codex-lb MUST be deployed automatically to the configured test server. The deploy workflow MUST pull the GHCR image tagged `:main` (which CI publishes on each push to `main` alongside `:sha-<sha>`) and recreate the `server` container by running `docker compose -f docker-compose.prod.yml pull server` followed by `docker compose -f docker-compose.prod.yml up -d --remove-orphans server`. Server-side secrets (notably `CODEX_LB_DATABASE_URL` and friends) MUST stay in the server-local `/srv/codex-lb/.env.local`; CI MUST NOT receive, materialize, or echo them. The deploy job MUST poll `http://127.0.0.1:2455/health/ready` for up to 60 s and treat non-2xx responses during that window as a deploy failure, capturing `docker compose logs --tail=200 server` in the job output for triage. A manual re-deploy via `workflow_dispatch` with an explicit `image_tag` input MUST remain available so an operator can re-roll a known-good commit SHA in one click without touching code.

#### Scenario: Push to main deploys after CI succeeds

- **GIVEN** a commit is pushed to `main`
- **AND** the push triggers `.github/workflows/ci.yml`
- **WHEN** `ci.yml` completes with conclusion `success`
- **AND** `ci.yml` publishes the GHCR tags `:main` and `:sha-<sha>`
- **THEN** the `.github/workflows/deploy-test.yml` workflow runs on `workflow_run`
- **AND** it SSHes to the configured test server as the deploy user
- **AND** it pulls the new `:main` image and recreates the `server` container via `docker compose`
- **AND** the deploy job exits successfully once `/health/ready` returns 2xx within the 60 s budget.

#### Scenario: Failed CI prevents a deploy

- **GIVEN** a commit is pushed to `main`
- **WHEN** `ci.yml` completes with a conclusion other than `success`
- **THEN** the deploy workflow does NOT trigger a `deploy` job
- **AND** the running container on the test server is not modified.

#### Scenario: Server-side secrets remain server-side

- **GIVEN** the workflow runs on a push to `main`
- **WHEN** the deploy SSH session runs `docker compose pull` and `up -d`
- **THEN** the workflow MUST NOT log, encrypt, or otherwise expose values from `/srv/codex-lb/.env.local`
- **AND** the only credentials that flow through CI are the deploy SSH key (`TEST_SSH_KEY`) and, if the GHCR package is private, the `GHCR_USER` / `GHCR_PACKAGES_READ_TOKEN` pair.

#### Scenario: Manual re-deploy targets a chosen SHA

- **GIVEN** the test server is running container version `:main` that corresponds to SHA `X`
- **WHEN** an operator triggers `workflow_dispatch` on `.github/workflows/deploy-test.yml` with `image_tag=sha-Y`
- **THEN** the deploy SSH session pulls `ghcr.io/<owner>/<repo>:sha-Y` from GHCR
- **AND** recreates the `server` container from that exact SHA
- **AND** exits successfully once `/health/ready` returns 2xx.

#### Scenario: Healthcheck timeout surfaces logs

- **GIVEN** a deploy attempt pulls and recreates the container
- **WHEN** `/health/ready` does not return 2xx within the 60 s budget
- **THEN** the deploy job exits non-zero
- **AND** captures the last 200 lines of `docker compose logs server` in the job output
- **AND** the previous container's state on the server is unchanged if the recreated container fails its own `docker compose` exit code (compose only swaps the running container on a healthy start).
