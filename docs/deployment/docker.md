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

The opt-in topology runs three private application backends, `blue`, `green` and `amber`,
behind HAProxy on public port `2455`. A fourth `surge` backend is stopped/weight zero outside
rollouts. HAProxy uses `leastconn` for new connections; requests within an established WebSocket
stay on its selected backend. Stock Compose remains single-replica.

Source of truth: [deployment requirements](../../openspec/specs/deployment-installation/spec.md)
and [operational context](../../openspec/specs/deployment-installation/context.md).

### Capacity profile and prerequisites

- Use shared PostgreSQL in `CODEX_LB_DATABASE_URL`; SQLite is rejected. Keep leader election enabled.
- Keep encryption material in the shared `codex-lb-data` volume.
- Each backend has a 3-GiB memory ceiling, including a 1-GiB aggregate native WebSocket queue
  budget. Four overlapping containers can therefore consume 12 GiB. On a roughly 16-GiB host,
  reserve the remainder for the OS, PostgreSQL, HAProxy, build processes and other services.
  A memory ceiling is not an allocation or a throughput guarantee.
- HA overrides each of two database pools to size 8 plus overflow 2: at most 20 connections per
  candidate replica, 80 across four. PostgreSQL's 100-connection setting is not changed.
  Legacy replicas retain their larger pools until replaced; monitor connection use/pool waits
  during the first migration. These overrides intentionally take precedence over `.env.local`.
- Native WebSocket queues account raw and decoded data together, with a 128-MiB per-socket cap.
  Buffer bytes are not whole-process RSS; JSON parsing, active messages and the native process
  require headroom. Slow consumers exceeding budget fail locally without penalizing an account
  or replaying ambiguously accepted work. Native HTTP buffering is a separate mechanism.
- Back up shared data and keep migrations rolling-compatible. Measure event-loop lag, CPU, RSS,
  queue-pressure errors, DB waits and upstream quota under realistic load before claiming capacity.

### Bootstrap and deployment

A first-time bootstrap requires explicit acknowledgement of the one-time public-port rebind:

```bash
./scripts/deploy-compose-ha.sh bootstrap
```

It checks all three backends before stopping the stock port owner and starting HAProxy. A failed
public-readiness check restores the stock server when present. Later deployments use only:

```bash
./scripts/deploy-compose-ha.sh deploy
./scripts/deploy-compose-ha.sh status
```

The repository's automatically discoverable `$codex-lb-ha-deploy` skill uses this script, waits
through all phases and verifies `blue,green,amber`, exactly three eligible base backends, surge
retired and public readiness. A deploy request does not authorize commit, push or rollback.

1. Build and activate surge after strict readiness.
2. Drain and replace blue, green and amber sequentially using the same candidate image.
   An established healthy 3+1 topology retains three eligible backends during replacement.
3. If the checked-in proxy configuration changed, validate it, snapshot runtime state, gracefully
   reload the HAProxy master and verify a new worker plus public readiness. Do not recreate the
   public-facing container. Existing connections remain with old workers.
4. Drain/stop surge and persist `blue,green,amber`.

Existing `blue`, `green` and `blue,green` markers migrate through the same command. Missing
servers are registered through the private Runtime API. Legacy migration preserves a two-backend
floor after surge activation, replaces the larger-pool legacy backends, then brings up amber.
It does not claim three-backend capacity before that topology is established.

The default per-backend drain bound is 300 seconds. Supply a positive second argument to change
it. If old HAProxy workers still exist after a graceful reload, the script conservatively waits
the full bound because their connections are not in the new worker's per-server counters.
A transient inability to read drain state stops replacement rather than treating it as zero.

An explicitly requested rollback can cancel the currently visible healthy base-backend drain:

```bash
./scripts/deploy-compose-ha.sh rollback 300
```

This aborts later replacements; it does not undo earlier replacements. Rollback is unavailable
during replacement, proxy reload or surge retirement. If cancellation happens during legacy
migration before amber exists, a `retained` phase keeps surge serving until a later deploy completes
the recorded candidate. A serving surge is never silently rebuilt/recreated.

A later `deploy` resumes a recorded retained-candidate, replacement, reload or retirement phase using the already
built candidate, without rebuilding source edits made after interruption. Inspect the intended
revision first. Failed proxy adoption leaves the base backends and surge serving for recovery.
If the running container's mounted configuration differs from the checkout, the script refuses
the reload; do not bypass this check with manual runtime commands.

“Zero downtime” refers to admission of new connections during healthy application rollout, not
unlimited lifetime for old streams. Connections can terminate at the bounded drain deadline.
An already degraded topology cannot promise three-backend capacity. The single host and HAProxy
remain one failure domain; this is not host-level HA.

Port `1455` is intentionally not proxied. Account onboarding that requires publishing the temporary
OAuth callback listener needs a separately planned maintenance window. Never publish backend
application ports during normal HA operation.

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
