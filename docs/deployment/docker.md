# Docker

## Basic run

```bash
docker volume create codex-lb-data
docker run -d --name codex-lb \
  -p 2455:2455 -p 1455:1455 \
  -v codex-lb-data:/var/lib/codex-lb \
  ghcr.io/soju06/codex-lb:latest
```

Ports:

- `2455` — dashboard + proxy API
- `1455` — OAuth login callback (needed while adding accounts)

The volume holds everything under `/var/lib/codex-lb/` (database, encryption key, archives) — back it up to preserve your data.

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

*Spec: [deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation)*
