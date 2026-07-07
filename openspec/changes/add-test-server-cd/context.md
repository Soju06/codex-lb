# Context — Test server continuous deployment

## Why a separate `:main` tag (not just `:latest`)

`release.yml` already publishes `:latest` for stable releases and
`:vX.Y.Z-beta.N` for prereleases. We deliberately do **not** point the
test-server deploy at `:latest`:

- `:latest` is owned by the release flow. Release-please mints new
  versions, the release workflow rebuilds + retags `:latest`. If a
  release is withdrawn (a draft recovers `publish-to-pypi` failures),
  `:latest` can move backwards in time.
- The deploy workflow needs a tag whose lifetime is **one CI run** for
  `:sha-<sha>` (good for rollback) and **moving but never regressing**
  for `:main` (good for "I want the latest green main").

Pointing deploy at `:main` + `:sha-<sha>` decouples test-server CD
from the versioned release flow. A bad release does not silently take
the test server with it; a bad main commit can be reverted by one
`workflow_dispatch` re-deploy pointing `image_tag=<last-good-sha>`.

## Why SSH, not self-hosted runner

The user explicitly chose SSH after comparing the two:

- Self-hosted runner would install `actions-runner` on
  `193.149.18.210` and run the deploy job directly on the box.
  Trade-offs:
  - **Pro:** no inbound SSH from the public internet; runner lives
    next to docker so `docker compose` is local.
  - **Con:** the runner process is permanent on a long-lived host;
    rotating its registration token is a separate chore; the runner
    queue runs out of the same machine that we want to redeploy,
    which is awkward during restarts.
- SSH from public GitHub Actions runners:
  - **Pro:** zero footprint on the server beyond OpenSSH + Docker;
    the deploy job runs in a clean ephemeral runner that finishes
    and disappears; re-deploys are 1-click `workflow_dispatch`.
  - **Con:** requires opening SSH (port 22/TCP) to the GitHub Actions
    IP allowlist. Acceptable for a test box.

The user selected SSH. If the server later moves behind a private
network or a tunnel, this choice can be revisited without changing the
deploy artifact (the script body) — only the transport step changes.

## Why `docker compose`, not raw `docker run` or Helm

- Helm would install a Kubernetes stack for a single-container proxy.
  Heavyweight; the on-server artefact (`deploy/helm/codex-lb/`) is
  designed for clusters, not a single bare-metal test box.
- Raw `docker run` loses the healthcheck, memory limits, named volume
  wiring, and `restart: unless-stopped` policy that
  `docker-compose.prod.yml` already encodes. Re-implementing them in a
  shell script duplicates compose for no win.
- `docker compose -f docker-compose.prod.yml` reuses the existing
  compose file (with the build → image edit) so healthcheck / restart /
  resource limits stay declarative and reviewable in the repository,
  not in a shell script.

The choice follows the project's existing deployment shape (compose
+ GHCR image, with the Helm chart for clusters).

## Server-side setup (admin / not committed)

These steps run once on `193.149.18.210`:

1. Install Docker Engine + `docker compose` plugin if not present:
   `apt-get install docker.io docker-compose-plugin` (or distro
   equivalent). Verify with `docker compose version`.
2. Create `/srv/codex-lb/`:
   ```sh
   mkdir -p /srv/codex-lb
   # copy docker-compose.prod.yml from the repo at the SHA where the
   # build→image change has landed
   touch /srv/codex-lb/.env.local      # populated with CODEX_LB_DATABASE_URL etc
   chmod 600 /srv/codex-lb/.env.local
   ```
   The named volume `codex-lb-data` (declared in the compose file) is
   created automatically on first `up`.
3. SSH access for CI:
   ```sh
   # generate a dedicated ed25519 deploy key (NOT a personal key)
   ssh-keygen -t ed25519 -N '' -f codex-lb-deploy -C codex-lb-gh-deploy
   # paste codex-lb-deploy.pub into ~/.ssh/authorized_keys
   # store the PRIVATE key as the TEST_SSH_KEY repository secret
   ```
   Restrict the key in `authorized_keys`:
   ```
   command="/usr/bin/docker compose -f /srv/codex-lb/docker-compose.prod.yml pull server && ..."
   ,no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty
   ssh-ed25519 AAAA… codex-lb-gh-deploy
   ```
   This locks the deploy key down to only the compose commands we
   actually run, even if the secret leaks. (Option we can add later.)
4. GHCR auth (only needed if the package stays private):
   - Make the package public: GitHub → Packages → `codex-lb` → Package
     settings → Change visibility → Public. Then no `docker login`
     step is needed and the workflow skip the `if
     secrets.GHCR_USER` block entirely.
   - Or keep it private: create a PAT with `read:packages` scope,
     store as `GHCR_PACKAGES_READ_TOKEN` secret; store any
     collaborator username as `GHCR_USER`. The deploy script logs in
     on every run.
5. Network access:
   - Allow inbound `22/TCP` from the GitHub Actions runner IP
     ranges. Fetched from `https://api.github.com/meta` (field
     `actions`). Update the allowlist whenever GitHub publishes a new
     range (no automation here yet — accept the manual refresh).

## Rollback procedure

```sh
# From the GitHub Actions UI or via gh CLI:
gh workflow run deploy-test.yml \
  -f image_tag=sha-7f4b9c2e1d8a5f6c...    # any green sha
```

The deploy script uses the same `docker compose pull && up -d` path,
so rolling back is the same shape as a forward deploy.

## Failure-mode log capture

When the post-deploy `/health/ready` poll times out, the script
attaches `docker compose logs --tail=200 server` to the job output so
the operator sees the last 200 lines of container stdout/stderr without
needing a second SSH session. The `set -euo pipefail` at the top keeps
the loop tight (1s sleep × up to 60 attempts).

## Not done in this change (follow-up if needed)

- Move SSH user from `root` to a dedicated `deploy` user with
  `sudo NOPASSWD: /usr/bin/docker compose …`. Worth doing before
  handing off to a wider audience.
- Auto-refresh of the GitHub Actions IP allowlist (script that
  pulls `/meta`, diffs, updates firewall rule via cloud API).
- Pinning to immutable `:sha-<sha>` tags instead of the moving `:main`
  tag — reduces the chance that a partially-cooked second deploy races
  a rollback. The `image_tag` input already supports pin mode; this is
  just a workflow-default flip.
- Sync the delta into the main spec (`openspec/specs/deployment-installation/spec.md`)
  via `openspec sync` after the change ships.
