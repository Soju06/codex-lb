# Docker

## Basic run

```bash
docker volume create codex-lb-data
docker network inspect codex-lb-net >/dev/null 2>&1 || docker network create codex-lb-net
docker run -d --name codex-lb \
  --network codex-lb-net \
  -p 2455:2455 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

Ports:

- `2455` — dashboard + proxy API
- `1455` — OAuth login callback inside the container; deliberately not published to the host by default

### OAuth sign-in from Docker

Codex Desktop also uses host port 1455 for its own ChatGPT login. Docker reserves every published port for the container's entire lifetime, even while no process is listening inside the container, so the portable setup leaves host port 1455 free.

When adding or reauthenticating a codex-lb account from Docker, choose the device-code method (recommended). Browser OAuth also works through the manual callback field: after authorization, copy the complete `http://localhost:1455/auth/callback?...` URL from the browser address bar and paste it into the dashboard dialog. A browser connection-error page is expected because the port is not published. Treat that URL as a secret and do not share it.

The dashboard's Windows port-forward helper is not compatible with the stock Docker mapping because server-side port 1455 is intentionally unpublished. Use device-code sign-in or the manual callback field instead; do not expose port 1455 over the network just to make that helper work.

On a dedicated machine where the dashboard browser runs locally, and where Codex Desktop or another OAuth callback consumer will never run, you can opt into automatic browser callbacks by adding `-p 127.0.0.1:1455:1455` to `docker run`. That mapping reserves host port 1455 whenever the container is running, even when no codex-lb browser flow is active.

If an existing container was created with the old `-p 1455:1455` mapping, updating its image cannot change that binding. Recreate the container without the mapping while keeping the same `codex-lb-data` volume. Compose users can update the Compose file and rerun the same `docker compose up` command with `--force-recreate`.

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

In the verified Docker Engine setup on Linux, host networking does not use `-p`; codex-lb listens on 2455 and can bind 1455 during browser OAuth. It also removes Docker's network-namespace isolation and can therefore conflict with Codex Desktop OAuth on the same host while that flow is active. The command is an opt-in path to a stable host resolver, not a DNS fix by itself.

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

## Auth mode examples

**Authelia / trusted header**

```bash
docker run -d --name codex-lb \
  -p 2455:2455 \
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
  -p 2455:2455 \
  -e CODEX_LB_DASHBOARD_AUTH_MODE=disabled \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

For Helm, pass the same values through `extraEnv`. What these modes mean and when to use them is covered in [Authentication](../authentication.md).

---

*Specs: [deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation) · [deployment-networking](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-networking)*
