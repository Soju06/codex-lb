# Rationale: Flexible Data Directory Resolution

## Decision

Change `_default_home_dir()` so that:
1. `CODEX_LB_DATA_DIR` overrides everything.
2. An existing `$HOME/.codex-lb` is preferred even when `/.containerenv` or `/.dockerenv` is present.
3. Only if neither applies, fall back to `/var/lib/codex-lb` inside containers.

## Why this order?

### Environment variable first (operator intent)

An explicit environment variable expresses deliberate operator intent. It must win over all heuristics so that bind-mounts, volume plugins, or custom images work without guessing path conventions.

### Existing home directory before container default (developer workflow)

Interactive containers (Podman rootless, devcontainers, `docker run -it`) often mount the user's home directory but do not run as root. In those environments `/var/lib/codex-lb` is either read-only or owned by root, causing a permission error on startup. If the user already ran codex-lb natively and has `~/.codex-lb/store.db`, re-using that directory inside the container is the least surprising behavior.

Only production-oriented images that run as root inside a dedicated volume should rely on `/var/lib/codex-lb`.

## Constraints & failure modes

- **Home directory not mounted into container**: If `~/.codex-lb` does not exist, the old behavior (`/var/lib/codex-lb`) is preserved, so existing Docker deployments are unaffected.
- **Env var points to missing directory**: The caller (Settings) will attempt to create files there and fail fast with a clear `PermissionError` or `OSError`, which is desirable — fail fast rather than silently falling back.

## Concrete example

A developer runs:

```bash
# 1. Native run
uv run codex-lb
# → creates ~/.codex-lb/store.db

# 2. Later, inside a rootless Podman container
podman run -v "$HOME:$HOME" -e HOME="$HOME" ghcr.io/Soju06/codex-lb:latest
```

Before this change: container detection triggers → `/var/lib/codex-lb` → permission denied.
After this change: `~/.codex-lb` exists → reused → no error, data preserved.
