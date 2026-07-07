---
name: sync-upstream
description: |
  Sync codex-lb fork with upstream Soju06/codex-lb.
  Performs safe PR-based sync via isolated git worktree.
  Auto-merges upstream-only conflicts; stops on fork-customized conflicts.
  Always files a sync-report audit issue; secrets are never written.
metadata:
  author: codex-lb
  version: "1.0.0"
  argument-hint: "[--dry-run]"
---

# Sync Upstream

Operational SSOT for syncing the `andrewmalov/codex-lb` fork with `Soju06/codex-lb`.
Invoked as `/sync-upstream` (interactive) or via `scripts/sync_upstream.sh` (scheduled,
non-interactive). The wrapper exports `GITHUB_TOKEN` from env, sets cwd to repo root,
and runs `claude -p "/sync-upstream"`.

You are an autonomous sync agent. Follow every step below in order. Do not skip
preflight, do not push to `main` directly, do not include secrets in any output.

## Arguments

- (none) — perform the full sync, push the branch, open the sync-PR, file the audit issue.
- `--dry-run` — run the full classification logic but do NOT push, create, or edit any
  GitHub PR/issue. Log every step. Exit code reflects classification as normal.

## Output (machine-readable wrapper)

Always end by emitting a single JSON line on stdout in this exact shape:

```json
{"status": "<up_to_date|auto_merged|stopped_blocker|preflight_failed|skipped_locked>", "reason": "<reason or empty>", "pr_url": "<url or empty>", "issue_url": "<url or empty>", "branch": "<branch or empty>"}
```

The wrapper parses this line and translates it to the exit codes defined below.

---

## Step 1: Preflight

Run every check, in order. On the first failure, abort with `preflight_failed` and a
specific `reason`. Capture `run_started_at` (ISO-8601, local) for the audit issue.

### 1.1 Auth check

```
gh auth status
```

If this fails, ALSO check:

```
git config --get-url.origin
```

If `gh auth status` reports a logged-in user OR the origin URL is reachable with the
credentials available (i.e. `git ls-remote origin main` exits 0), continue. Otherwise
abort with `preflight_failed` reason `no_auth`.

### 1.2 Branch check

```
git rev-parse --abbrev-ref HEAD
```

Must equal `main`. If not, abort with `preflight_failed` reason `wrong_branch`. Capture
the actual branch name for the audit issue body.

### 1.3 Working tree check

```
git status --porcelain
```

Must be empty. If not, abort with `preflight_failed` reason `dirty_tree`. Capture the
status output for the audit issue body.

### 1.4 Lock file check

```
test -e /tmp/codex-lb-sync.lock
```

If the lock file exists, exit with status `skipped_locked` (exit code 3) and NO
side-effects — do not file an audit issue, do not perform any fetch. The presence of
the lock file is a normal race condition; an existing concurrent run will produce its
own audit issue.

Otherwise, write the current PID to the lock file:

```
echo $$ > /tmp/codex-lb-sync.lock
```

### 1.5 Upstream remote check

```
git remote get-url upstream
```

- If the command errors (remote missing): add the remote:
  ```
  git remote add upstream https://github.com/Soju06/codex-lb.git
  ```
- If the URL does not exactly equal `https://github.com/Soju06/codex-lb.git`: abort
  with `preflight_failed` reason `upstream_remote_wrong_url`. Capture the existing URL.

### 1.6 Fetch with retry

```
git fetch upstream main
```

On failure, retry with exponential backoff (5s, then 10s, then 20s — 3 retries total
including the initial attempt). If all attempts fail, abort with `preflight_failed`
reason `upstream_fetch_failed`. Capture the last error message.

Preflight failures from steps 1.1, 1.2, 1.3, 1.5, or 1.6 each file ONE audit issue
(see Step 7). Preflight failure from step 1.4 (`skipped_locked`) does NOT file an
audit issue — the concurrent run will file its own.

---

## Step 2: Fetch and compare

```
diff_stat=$(git diff --shortstat origin/main..upstream/main)
new_commits=$(git log --oneline origin/main..upstream/main)
```

If `diff_stat` is empty (i.e. `origin/main` already contains every commit on
`upstream/main`):

1. Release the lock: `rm -f /tmp/codex-lb-sync.lock`
2. Emit `{"status":"up_to_date", ...}` with empty `pr_url` and `issue_url`.
3. Exit with status `up_to_date` (exit code 0).
4. Do NOT open a sync-PR. Do NOT file an audit issue. The "no change" case is the
   happy path and produces no audit noise.

---

## Step 3: Isolated worktree merge

Set up paths for today:

```
DATE=$(date +%F)                            # YYYY-MM-DD in local time
WORKTREE_PATH="/tmp/codex-lb-sync-${DATE}"
BRANCH_NAME="sync/upstream-${DATE}"
```

Create the worktree and a fresh branch based on `origin/main`:

```
git worktree add "$WORKTREE_PATH" -b "$BRANCH_NAME" origin/main
```

All subsequent steps in this run operate inside `$WORKTREE_PATH` only. The
operator's working copy on `main` remains untouched.

Inside the worktree:

```
git merge --no-ff upstream/main
```

Capture exit code and stderr.

---

## Step 4: Merge classification

Inspect the merge result. Compute the fork's customization surface dynamically:

```
fork_surface=$(git diff --name-only origin/main..HEAD)
```

(This is the set of paths the fork's `main` has touched that upstream has NOT —
i.e. the fork's local customizations.)

Also extract the set of files reported as conflicted by `git merge`:

```
conflicted=$(git diff --name-only --diff-filter=U)
```

Classification:

- **`clean`** — no conflicts at all, OR every conflicted file is NOT in
  `$fork_surface`. For any conflicts, resolve them in favor of `upstream`
  (`git checkout --theirs <file>` for the conflicted paths), then
  `git add` them and continue. After resolution, the merge commit must succeed
  (`git commit --no-edit` if a merge was already started, otherwise complete the
  merge normally).
- **`blocked`** — at least one file is in BOTH `$conflicted` AND
  `$fork_surface`. Stop immediately. Do NOT resolve. Do NOT push. Do NOT open a
  sync-PR.

### Clean path

Continue to Step 5 (push) and Step 6 (sync-PR).

### Blocked path

1. Generate a `patch.diff` of the conflict state:
   ```
   git diff > "$WORKTREE_PATH/patch.diff"
   ```
2. Capture the list of conflicting files.
3. Skip directly to Step 7 (audit issue) with `status=stopped_blocker`. Steps 5
   and 6 do NOT run.
4. Continue to Step 8 (cleanup).

---

## Step 5: Push (clean path only)

```
git push -u origin "$BRANCH_NAME"
```

The only allowed refspec for this skill is `HEAD:sync/upstream-<DATE>`. Do NOT push
to `main`. Do NOT rebase or rewrite history on any branch.

---

## Step 6: sync-PR (clean path only)

Check whether a sync-PR already exists for today:

```
existing_pr=$(gh pr list --head "$BRANCH_NAME" --json url --jq '.[0].url // ""')
```

### 6a. New PR

If `existing_pr` is empty:

```
gh pr create \
  --base main \
  --head "$BRANCH_NAME" \
  --title "chore(sync): upstream/main as of ${DATE}" \
  --body "$PR_BODY"
```

### 6b. Edit existing PR (idempotent re-run on the same day)

If `existing_pr` is non-empty:

```
gh pr edit "$BRANCH_NAME" \
  --title "chore(sync): upstream/main as of ${DATE}" \
  --body "$PR_BODY"
```

### PR body template

The PR body MUST contain ALL of the following:

1. **Diffstat** — the exact output of `git diff --shortstat origin/main..upstream/main`
   (or its post-merge equivalent) verbatim.
2. **Commit list** — a bulleted list, one bullet per upstream commit, each formatted
   as ``- `<sha>` <subject>`` (use `git log --oneline origin/main..upstream/main`
   output).
3. **Upstream HEAD SHA** — `git rev-parse upstream/main`.
4. **Local origin/main SHA at run start** — captured before the merge.

The body MUST NOT contain `GITHUB_TOKEN`, any `Authorization:` header, or any token
value. Treat the entire PR body as a public artifact.

The sync-PR goes through the project's normal merge gates (CI green, `@codex review`
clean, `mergeable=CLEAN`) per `.github/CONTRIBUTING.md`. Do NOT bypass these gates and
do NOT fast-forward `main` directly.

Capture the resulting `pr_url`.

---

## Step 7: Audit issue (always for non-no-op runs)

Every run that is NOT `up_to_date` and NOT `skipped_locked` MUST produce exactly ONE
GitHub issue in the fork. Use `gh issue create`.

### Title

- Success (status `auto_merged`):
  `sync-upstream report <YYYY-MM-DD>`
- Failure:
  `sync-upstream <status> <YYYY-MM-DD>` where `<status>` is one of
  `stopped_blocker`, `preflight_failed`.

### Labels

Always apply `sync-report`. Additionally:
- `sync-blocker` — when `status=stopped_blocker`
- `sync-failure` — when `status=preflight_failed`

If the label does not already exist, create it first (`gh label create <name>
--description "<desc>" --color <hex>`) before applying.

### Body (success / auto_merged)

- Status: `auto_merged`
- Run start / end timestamps (ISO-8601, local)
- Upstream HEAD SHA (`upstream/main` post-fetch)
- Local `origin/main` SHA at run start
- Diffstat of the merged range
- Link to the sync-PR (`pr_url`)

### Body (stopped_blocker)

- Status: `stopped_blocker`
- Run start / end timestamps
- Upstream HEAD SHA
- Local `origin/main` SHA at run start
- Diffstat of the would-have-been merged range
- Link to the sync-PR (or note that none was opened)
- **Conflicting files** — bulleted list of paths
- **Patch** — fenced ```` ```diff ```` block containing `$WORKTREE_PATH/patch.diff`
- **Reproduction commands** — the exact shell snippet the operator can use to finish
  the merge manually:
  ```
  cd /tmp
  git clone https://github.com/andrewmalov/codex-lb.git sync-repro
  cd sync-repro
  git remote add upstream https://github.com/Soju06/codex-lb.git
  git fetch upstream main
  git checkout -b sync/upstream-<DATE> origin/main
  git merge --no-ff upstream/main
  # resolve manually, push, open a new sync-PR
  ```

### Body (preflight_failed)

- Status: `preflight_failed`
- The specific `reason` (one of `no_auth`, `wrong_branch`, `dirty_tree`,
  `upstream_remote_wrong_url`, `upstream_fetch_failed`)
- Captured stderr of the failing command
- Run start / end timestamps
- Upstream HEAD SHA (if fetched)
- Local `origin/main` SHA

### Secrets

The agent MUST NOT include `GITHUB_TOKEN`, `Authorization` headers, or any token
value present in the agent's environment in any issue or PR body. This is a hard
constraint — sanitize before writing.

Capture the resulting `issue_url`.

---

## Step 8: Cleanup (always)

1. Remove the worktree (if it was created):
   ```
   git worktree remove --force "$WORKTREE_PATH"
   ```
2. Release the lock:
   ```
   rm -f /tmp/codex-lb-sync.lock
   ```
3. Emit the final JSON line on stdout (see "Output" section above).

If the cleanup itself fails, log the failure to stderr but do NOT block the exit.
The next run's preflight will surface any leftover worktree / lock.

---

## Exit codes

The wrapper translates the emitted `status` field into these exit codes:

| Status              | Exit code | Meaning                                                     |
|---------------------|-----------|-------------------------------------------------------------|
| `up_to_date`        | 0         | No upstream changes; nothing to do                         |
| `auto_merged`       | 0         | Sync-PR opened (or updated); operator must review and merge |
| `preflight_failed`  | 1         | Auth, branch, dirty tree, lock, remote, or fetch failure    |
| `stopped_blocker`   | 2         | Conflicts in files the fork also touches; operator action   |
| `skipped_locked`    | 3         | Another sync was running; no side-effects                   |

---

## Dry-run mode (`--dry-run`)

When the invocation includes `--dry-run`:

- Run preflight (1.1-1.6) normally — these are read-only and safe.
- Run fetch and compare (Step 2) normally.
- Skip worktree creation in Step 3. Instead, dry-run the merge classification logic
  in-memory by simulating `git merge --no-ff upstream/main` against a temp branch
  if practical; if simulation is impractical, perform the merge in the worktree but
  do NOT push and do NOT open a sync-PR.
- Skip Step 5 (push) entirely.
- Skip Step 6 (sync-PR create / edit) entirely. Log the would-be PR title and body
  to stdout for the operator to inspect.
- For Step 7 (audit issue): do NOT call `gh issue create`. Instead, print the would-be
  issue title, labels, and body to stdout.
- Always perform Step 8 (cleanup).
- Emit the same final JSON shape. Exit code reflects classification as normal.

---

## Out-of-scope guarantees

You MUST NOT, in any branch of this skill:

- Push directly to `main` on `origin`. Push targets are `sync/upstream-*` branches only.
- Rebase or rewrite history on any branch.
- Modify the operator's feature branches (`feature/*`, `fix/*`, `release/*`).
- Delete or archive anything in `openspec/changes/`.
- Touch proxy, account, dashboard, or release-automation code; this capability is
  read-only on application code outside of the merge itself.
- Open sync-PRs whose base is anything other than `main` on the fork.
- Include `GITHUB_TOKEN`, any `Authorization:` header, or any token value in any
  issue or PR body.

---

## Related

- `openspec/changes/add-upstream-sync-cron/specs/upstream-sync/spec.md` — normative spec.
- `openspec/changes/add-upstream-sync-cron/context.md` — rationale, decisions, examples.
- `scripts/sync_upstream.sh` — wrapper that invokes this skill via `claude -p`.
- `.github/CONTRIBUTING.md` — merge gates the sync-PR must pass.
- `CLAUDE.md` — `GITHUB_TOKEN` env var convention.