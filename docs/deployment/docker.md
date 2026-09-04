# Docker

## Basic run

```bash
docker volume create codex-lb-data
docker network inspect codex-lb-net >/dev/null 2>&1 || docker network create codex-lb-net
docker run -d --name codex-lb \
  --network codex-lb-net \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

Ports:

- `2455` — dashboard + proxy API
- `1455` — OAuth login callback (needed while adding accounts)

The volume holds everything under `/var/lib/codex-lb/` (database, encryption key, archives) — back it up to preserve your data.

## Switching Wi-Fi or other networks

When a laptop switches from one Wi-Fi network to another—for example, from home Wi-Fi to a phone hotspot—or when a VPN connects or disconnects, existing internet connections may briefly break. Docker can also keep using a DNS server from the previous network. DNS is the service that finds the network address for names such as `chatgpt.com`; if Docker's copy is out of date, codex-lb may report timeouts while contacting OpenAI even though the host browser works.

codex-lb retries only when the transport can prove that the request failed before it was sent. Merely seeing no output is not enough: if a request may already have reached OpenAI, codex-lb returns the network error without resending it, which avoids accidentally starting the same response twice. In either case, it avoids treating a laptop-wide DNS problem as a problem with an individual account. It cannot, however, repair a Docker DNS service that remains pointed at the old network.

For laptops that switch networks frequently:

- **Simplest on Linux, macOS, and Windows:** run `uvx codex-lb` directly on the host. This avoids Docker's additional DNS layer.
- **Docker Engine on Linux (verified with `systemd-resolved`):** use host networking so the container shares the host resolver path. This survives network switches only when the host exposes a stable resolver address, such as the `127.0.0.53` `systemd-resolved` stub. If the host's `/etc/resolv.conf` points directly to a DNS server supplied by Wi-Fi or other DHCP, that address can still become stale. In that case, configure a stable host resolver, follow the [bridge-listener runbook](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/deployment-networking/context.md#diagnostics-and-recovery), or prefer `uvx`. Use the following command instead of the portable Docker command above.
- **Docker Desktop on macOS or Windows:** Docker Desktop 4.34 and later offers opt-in host networking, but containers still run through Docker Desktop's virtual machine and its DNS behavior can vary by version and configuration. This setup has not been verified as a reliable fix for switching networks. Keep Docker Desktop current; if failures persist, prefer the native `uvx` installation.

```bash
docker volume create codex-lb-data
docker run -d --name codex-lb \
  --network host \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

In the verified Docker Engine setup on Linux, host networking does not use `-p`; codex-lb still listens on ports 2455 and 1455. It also removes Docker's network-namespace isolation. The command is an opt-in path to a stable host resolver, not a DNS fix by itself.

## Docker Compose

For a production-shaped Compose setup (watchtower-friendly tags, external PostgreSQL via env), start from
[`docker-compose.prod.yml`](https://github.com/Soju06/codex-lb/blob/main/docker-compose.prod.yml) — it defines
only the `server` service. The optional `postgres` / `postgres-upgrade` profiles live in the root
[`docker-compose.yml`](https://github.com/Soju06/codex-lb/blob/main/docker-compose.yml) (see [Database](../database.md)):

```bash
cp .env.example .env.local   # required: the compose file references .env.local via env_file — an unedited copy still runs with zero config
docker compose -f docker-compose.prod.yml up -d
```

For PostgreSQL profiles and the Postgres 16 → 18 upgrade runbook, see [Database](../database.md).

## Active-active deployment with HAProxy

The opt-in HA Compose topology keeps HAProxy bound to public port `2455` while `server-blue` and
`server-green` both receive traffic at equal weight. This spreads normal CPU load across two
application processes. `server-surge` is a third private backend that remains stopped and at weight
zero outside deployments. A healthy rollout activates surge first, replaces blue and green one at
a time, then retires surge. The stock Compose files above remain the simpler single-replica option.

This topology has multi-replica prerequisites. Before bootstrapping it:

- Set `CODEX_LB_DATABASE_URL` in `.env.local` to one shared PostgreSQL database. SQLite is rejected.
- Keep leader election enabled; omit `CODEX_LB_LEADER_ELECTION_ENABLED` or set it to `true`.
- Keep the encryption key in the shared `codex-lb-data` volume. The default
  `/var/lib/codex-lb/encryption.key` does this automatically.
- Reserve enough host CPU and memory for three application containers during a deployment. The
  checked-in limit permits up to 1 GiB per application container; Docker and HAProxy also need
  headroom.
- Back up the database and `codex-lb-data` volume, and use expand/contract database migrations that
  remain compatible while the old and new application versions overlap.

From the repository root, migrate an existing stock Compose deployment once:

```bash
./scripts/deploy-compose-ha.sh bootstrap
```

The command builds and checks both blue and green before stopping the stock server. Rebinding port
`2455` from the stock container to HAProxy causes one short interruption during this initial
topology migration. If HAProxy fails its public readiness check, the script restarts the previous
stock server. Later healthy deployments do not restart the public HAProxy listener:

```bash
./scripts/deploy-compose-ha.sh deploy
./scripts/deploy-compose-ha.sh status
```

For Codex-driven operations, the repository includes the implicitly discoverable
`$codex-lb-ha-deploy` skill. After this one-time bootstrap, a request such as “deploy the current
changes” automatically uses the command above, waits for every drain phase, and verifies blue plus
green are eligible, surge is retired, and public readiness succeeds. The skill does not commit or
push code. On an uninitialized host it asks for acknowledgement before bootstrap because the
initial public-port rebind is not downtime-free.

The deployment sequence is:

1. Build and start surge; require container, backend, and public readiness before changing a base
   backend.
2. Give surge positive weight. At least two healthy backends must now be eligible.
3. Set blue to weight zero, drain it, replace it from the already-built candidate image, check it,
   and restore positive weight while green and surge keep serving.
4. Repeat for green while blue and surge keep serving.
5. Set surge to weight zero, drain and stop it, then persist `blue,green` as the serving topology.

The first deployment after upgrading an existing single-active HA host also follows this flow. If
the running HAProxy process does not yet declare surge, the script registers surge through the
private HAProxy Runtime API; it does not restart or rebind the front door.

The default HAProxy drain window is 300 seconds. Pass a positive number of seconds as the second
argument to `bootstrap`, `deploy`, or `rollback` when a different bound is required. During a
visible blue or green drain, a second terminal can cancel that drain and abort the rest of the
rollout:

```bash
./scripts/deploy-compose-ha.sh rollback 300
```

Rollback is unavailable after that backend stops or while surge is retiring. If an earlier backend
was already replaced, rollback leaves a safe mixed-version `blue + green` state; deploy the desired
revision as the next rollout instead. If replacement startup fails after an old backend stops, the
other base backend and surge remain eligible, `status` reports the incomplete phase, and running
`deploy` again resumes it from the candidate image already built for that rollout. Source edits made
after the failure are not rebuilt during this recovery; finish the recorded rollout before
deploying another revision.

Here, “zero downtime” means continuous admission of new HTTP, SSE, and WebSocket connections during
a healthy application cutover. “Capacity-preserving” means at least two healthy application
backends remain eligible after surge activates. A topology that is already unhealthy or degraded
cannot guarantee that two-backend capacity. Connections already assigned to a draining backend can
be terminated when the configured HAProxy drain bound and the application's bounded SIGTERM drain
expire. The Docker host, Docker daemon, and single HAProxy container remain one front-door failure
domain; use the Helm multi-replica deployment across failure domains when host-level availability
is required.

Port `1455` is intentionally not proxied because the OAuth callback listener is opened temporarily
inside one application process. Add accounts before migrating, or perform account onboarding in a
planned maintenance window with port `1455` published directly to one base backend. Never publish
the backend application ports during normal HA operation.

## Auth mode examples

**Authelia / trusted header**

```bash
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -e CODEX_LB_DASHBOARD_AUTH_MODE=trusted_header \
  -e CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER=Remote-User \
  -e CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS=true \
  -e CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS=172.18.0.0/16 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

**Hard override / no app-level dashboard auth**

```bash
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -e CODEX_LB_DASHBOARD_AUTH_MODE=disabled \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

For Helm, pass the same values through `extraEnv`. What these modes mean and when to use them is covered in [Authentication](../authentication.md).

---

*Specs: [deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation) · [deployment-networking](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-networking) · [replica-operations](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/replica-operations)*
