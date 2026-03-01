# Sync Runbook (Fork + Upstream, No Feature Loss)

This runbook keeps custom codex-lb features safe while regularly ingesting upstream updates.

## Branch and Remote Model

- `origin` = your fork (production source of truth)
- `upstream` = official project (`Soju06/codex-lb`)
- `main` = stable branch used for deploy/promote
- `codex/sync-upstream-YYYYMMDD` = temporary integration branch
- `codex/feature-*` = custom feature branches

Verify once per clone:

```bash
git remote -v
# Expect:
# origin   git@github.com:<you>/codex-lb.git
# upstream https://github.com/Soju06/codex-lb.git
```

## Rules (Hard Requirements)

1. Never hard-reset `main` to upstream.
2. Never deploy untested merge results to main.
3. Always test in canary first, then promote.
4. Keep custom behavior in small, labeled commits (`[custom] ...`).
5. Keep a must-keep list current (see [Custom Features Checklist](#custom-features-checklist)).

## Standard Sync Procedure

### 1) Prepare

```bash
git checkout main
git pull --ff-only origin main
git fetch upstream
```

### 2) Create sync branch

```bash
SYNC_BRANCH="codex/sync-upstream-$(date +%Y%m%d)"
git checkout -b "$SYNC_BRANCH"
```

### 3) Merge upstream

```bash
git merge upstream/main
```

If conflicts happen:

- Resolve conflict-by-conflict.
- Preserve required custom behavior.
- Do not accept upstream version blindly for custom files.

### 4) Validate locally

```bash
# Python app
uv sync
uv run pytest

# Frontend (if touched)
cd frontend
bun install
bun run build
cd ..
```

If tests are partial, document exactly what was run and what was skipped.

### 5) Canary deploy and smoke test

Deploy sync branch to canary only.

Minimum smoke matrix:

1. `GET /health`
2. `POST /v1/responses` (store true/false)
3. `POST /v1/embeddings`
4. dashboard load + settings save
5. force-model behavior visible in logs
6. actor logging (ip/app/api key) visible in logs
7. Home Assistant plugin (`codex_lb.generate`, `codex_lb.generate_data`)

### 6) Merge to fork main only after canary pass

```bash
git checkout main
git merge --no-ff "$SYNC_BRANCH"
git push origin main
```

### 7) Promote runtime (no downtime flow)

- Keep main container running.
- Promote via tested canary image/container handoff.
- Validate endpoints immediately post-promotion.

## Emergency Rollback

If regression appears after promotion:

1. Repoint traffic to last known-good image/container.
2. Revert merge commit on `main`:

```bash
git checkout main
git log --oneline -n 20
# identify bad merge commit SHA

git revert -m 1 <merge_sha>
git push origin main
```

3. Redeploy previous good revision.
4. Open follow-up fix branch from current `main`.

## Custom Features Checklist

Before accepting an upstream sync, explicitly verify these remain intact:

- Round-robin account routing behavior.
- Force-model override rules (global + per actor) and reasoning effort enforcement.
- Actor logging (ip/app/api key/model) in logs/dashboard.
- Store/chaining compatibility layer (`store=true`, `previous_response_id`).
- Embeddings routing and health.
- Home Assistant integration behavior.

Keep this checklist updated whenever new custom behavior is added.

## PR Policy

When syncing upstream:

1. Push sync branch to fork.
2. Open PR in fork first (`sync branch -> main`).
3. Require at least one canary evidence comment (commands + results).
4. Merge only after checklist is fully green.

## Team Conventions

- Use branch names with `codex/` prefix.
- Keep commits scoped and descriptive.
- Prefer non-interactive git commands.
- Never force-push shared `main`.

