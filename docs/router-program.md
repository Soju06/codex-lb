# Router program: a self-healing, learning router for Claude Code orchestration

Owner: Alex. Coordinator: Fable driver session `agent-rails-2f`. Started 2026-09-19.

## Why

Alex, 2026-09-19: "we should use opus as much as we want since we only ever run
out of fable limit and that's mutually exclusive in some ways ... set up the
tooling so we can have a truly perfect and self healing and learning router
through claude code to actually achieve tasks as an orchestrator."

Measured the same day:

- Fable is the binding pool. Two of five Anthropic accounts are Fable-eligible;
  one has 13% of its week left. Opus is the volume seat (2148 requests/24h).
- The LB does track Anthropic's Fable-scoped weekly percent per account
  (`additional_usage_history`, key `anthropic_fable_scoped_weekly`, refreshed
  every few minutes) but never serializes it on `/api/accounts`; only the
  boolean `fableEligible` is exposed. Live at 21:33 UTC: four of five
  accounts are at 99–100% Fable-scoped; only `aneym@kineticapps` (6% used)
  has Fable headroom. `alex@kineticapps` at 99% still reads eligible because
  the scoped threshold defaults to 100.
- The LB cannot tell a driver request from a subagent request (same session
  id, no seat column), so routing health cannot be computed from the LB alone.
- `routing-pulse.py` is not wired into settings.json. `Explore` pins `sonnet`,
  which resolves to `claude-sonnet-4-6` because the launcher deliberately
  leaves the Sonnet default unset.
- A Fable driver turn costs context × turns: one session spent 47 Fable
  requests / 7.5M cached tokens in 25 minutes mostly reading and polling.
- Idle capacity: Cursor subscription (Grok 4.6, Sol, Luna, Composer, Opus 5,
  Sonnet 5 on Cursor's quota), Kimi and GLM at 100%.

## Routing rule (the policy this program enforces)

1. Fable is the driver and the judge. Nothing else runs on Fable: no forks
   unless the context is the deliverable, no catch-all subagents inheriting it.
2. Opus 5 is the default seat for everything that needs a Claude model. It is
   not rationed. Opus traffic prefers `burn_first` accounts (those past the
   Fable threshold) so Fable headroom on the other accounts is preserved.
3. Read-only exploration goes to the cheapest live seat: Explore on
   `claude-sonnet-5` (pinned by full id), Cursor Grok low/fast, or Kimi.
4. Verification is cross-vendor: the verifier never shares a vendor with the
   author of the diff. Opus-authored work is verified on Codex (Sol/Astra) or
   Cursor Sol; Codex-authored work is verified on Opus.
5. Every dispatch and every closeout is recorded. Defaults change only from
   recorded outcomes (`route learn`), never from opinion.
6. A seat that is down is routed around automatically (`route doctor` writes
   the live fallback chain); a pool that is low is reported before it is empty.

## Frozen contracts

### C1. `GET /api/pools` (LB)

```json
{
  "generatedAt": "2026-09-19T21:00:00Z",
  "pools": [
    {
      "id": "anthropic-fable",
      "provider": "anthropic",
      "kind": "fable_scoped",
      "accounts": 5,
      "eligibleAccounts": 2,
      "headroomPercent": 13.0,
      "aggregateRemainingPercent": 53.0,
      "resetAt": "2026-09-23T11:00:00Z",
      "status": "low",
      "source": "scoped_marker" 
    },
    { "id": "anthropic-general", "provider": "anthropic", "kind": "weekly", "...": "same fields" },
    { "id": "openai-codex", "provider": "openai", "kind": "weekly", "...": "same fields" },
    { "id": "kimi", "provider": "kimi", "kind": "weekly", "...": "same fields" },
    { "id": "glm", "provider": "glm", "kind": "weekly", "...": "same fields" }
  ]
}
```

- `headroomPercent` = the best single account's remaining percent for that
  pool (what one more request can actually use). `aggregateRemainingPercent`
  = mean remaining across usable accounts.
- `status`: `ok` (headroom ≥ 25), `low` (5 ≤ headroom < 25), `exhausted`
  (< 5 or no usable account).
- `source` for the Fable pool: `scoped_marker` when a fresh (≤6h)
  Fable-scoped marker exists on at least one account, else `weekly_heuristic`.
- Dashboard-session auth, same as `/api/sessions`. No secrets in the body.

### C2. Dispatch ledger (local, JSONL)

Path `~/.claude/logs/dispatch.jsonl`, one JSON object per line, append-only:

```json
{"ts":"2026-09-19T21:00:00Z","event":"dispatch","session_id":"...","subagent_type":"opus-seat","model":"claude-opus-5","name":"lb-limits-scout","task_class":"explore","cwd":"/...","prompt_sha256":"<sha256 of the Agent prompt>","fork":false}
{"ts":"2026-09-19T21:04:00Z","event":"closeout","session_id":"...","agent_type":"opus-seat","subagent_type":"opus-seat","prompt_sha256":"<recomputed from the subagent transcript>","match":"prompt_hash","duration_s":240,"ok":true,"error":null}
```

- `dispatch` is written by the PreToolUse Agent hook (seat-guard), whether or
  not it denies; a denied dispatch carries `"denied":true` and the reason.
- `closeout` is written by a SubagentStop hook. `ok` is false when the
  subagent ended with an error or the transcript's last message reports a
  failure; `task_class` is copied from the matching dispatch when present.
- `task_class` is taken from an optional `[class:<x>]` tag at the start of the
  Agent prompt, else inferred from `subagent_type` via the routing table.

### C3. Routing table `config/coding-agents/routing-table.json`

```json
{
  "version": 1,
  "classes": {
    "plan":      {"driver": true},
    "review":    {"chain": [{"seat":"plan-reviewer","model":"claude-planner"}]},
    "explore":   {"chain": [{"seat":"Explore","model":"claude-sonnet-5"},{"seat":"cursor-seat","model":"cursor-grok-4.6-low-fast"},{"seat":"opus-seat","model":"claude-opus-5"}]},
    "implement": {"chain": [{"seat":"opus-seat","model":"claude-opus-5"},{"seat":"implementer","model":"gpt-6-astra"},{"seat":"cursor-seat","model":"gpt-5.6-sol-high"}]},
    "mechanical":{"chain": [{"seat":"cursor-seat","model":"cursor-grok-4.6-medium-fast"},{"seat":"opus-seat","model":"claude-opus-5"}]},
    "verify":    {"cross_vendor": true, "chain": [{"seat":"verifier","model":"claude-opus-5","vendor":"anthropic"},{"seat":"codex-verifier","model":"gpt-5.6-sol-xhigh","vendor":"openai"},{"seat":"cursor-seat","model":"gpt-5.6-sol-xhigh","vendor":"cursor"}]},
    "computer":  {"chain": [{"seat":"computer-use","model":"gpt-6-astra"}]}
  },
  "pools": {"claude-opus-5":"anthropic-general","claude-sonnet-5":"anthropic-general","claude-planner":"anthropic-fable","gpt-6-astra":"openai-codex","gpt-5.6-sol-high":"openai-codex","gpt-5.6-sol-xhigh":"openai-codex","cursor-*":"cursor","kimi-*":"kimi","glm-*":"glm"},
  "overrides": []
}
```

`overrides` entries are written only by `route learn --apply` and carry
`{class, demote, reason, evidence, ts}`.

### C4. `route` CLI (`clients/route`, Python 3 stdlib only)

- `route pools [--json]`: C1, pretty or raw.
- `route pick <class> [--author-vendor V] [--json]`: first chain entry whose
  seat is up (per `~/.claude/routing-state.json`) and whose pool is not
  `exhausted`, honoring `cross_vendor` and `overrides`. When pool state is
  unreachable (no LB, no cache) the pool counts as `unknown` and is not
  skipped: a router that refuses because its telemetry is down is worse than
  one that routes and says so; the pick records `pool_status: "unknown"`. Prints
  `{seat, model, pool, reason, fallbacks}`. Exit 2 when nothing is routable.
- `route doctor [--write]`: probes LB `/api/health`, `/api/pools`,
  `cursor-agent status`, `codex-companion.mjs status`, and one minimal LB
  request per non-Fable Claude seat model. Writes
  `~/.claude/routing-state.json` `{ts, seats:{name:{ok,latency_ms,error}}, pools}`
  and `~/.claude/routing-ALERT` (text, like `~/.jev/ALERT`) when degraded.
  Exit 1 when degraded. Installed as a launchd job every 30 minutes.
- `route report [--days N]`: joins C2 with LB session analytics: per
  class × seat × model: dispatches, closeout ok-rate, median duration,
  cost. Markdown to stdout.
- `route learn [--apply]`: from the report, proposes C3 overrides by rule:
  n ≥ 10 closeouts in the window and ok-rate < 0.6 → demote that entry to the
  end of its chain. Without `--apply` prints the proposed diff only.

## Seats and hooks (canonical in this repo, installed by `install-policy.py`)

- `agents/cursor-seat.md`: Bash-only forwarder. Runs
  `cursor-agent -p --force --trust --output-format json --model <m> [-w <lane>] "<contract>"`.
  Proven headless 2026-09-19 (10.8s round trip on `cursor-grok-4.6-medium-fast`).
  Never `claude-fable-*` on Cursor (no ZDR).
- `agents/codex-verifier.md`: Bash-only forwarder to codex-companion in
  read-only mode, `gpt-5.6-sol-xhigh` (aliased through the LB), with the
  verifier procedure from `verifier.md`.
- `agents/Explore.md`: `model: claude-sonnet-5`.
- `agents/verifier.md`: brief must name the author vendor; refuse when equal.
- `hooks/seat-guard.py`: Opus stays allowed. Deny only an explicit
  `claude-fable-*` model or a catch-all type inheriting the driver. Allow
  `fork` but write it to the ledger with `"fork":true`. Writes C2 `dispatch`.
- `hooks/subagent-closeout.py` (SubagentStop): writes C2 `closeout`.
- `hooks/routing-pulse.py`: rewired into `UserPromptSubmit`. Triggers on Fable
  requests/hour above a threshold, or on ledger ratio (closeouts vs Fable
  requests) below a floor. Message names `route pick`.
- `install-policy.py` installs hooks and the settings.json hook entries
  idempotently, and the launchd plist for `route doctor`.

## LB changes

- L1. Expose the Fable-scoped window on `/api/accounts`: add
  `fableScopedWeekly {usedPercent, resetAt, recordedAt, fresh}` to
  `AccountSummary` (`app/modules/accounts/schemas.py`), populated in
  `_account_to_summary` (`mappers.py:127,300`), which already receives the
  window and only uses it for the boolean. The pipeline itself
  (`app/core/clients/anthropic_usage.py:125-137` → `updater.py:529-548` →
  `anthropic_service.py:1033-1049`) is real and fresh; nothing to build there.
- L2. `GET /api/pools` (C1) in `app/modules/accounts` or a new
  `app/modules/pools` module, unit-tested.
- L3. Fable reserve: `anthropic_fable_scoped_max_used_percent` default 90 so
  lanes stop at 90% and interactive Fable keeps the last 10%.
- L4. Dashboard: a pools strip on the sessions page reading C1 (frontend,
  optional; API first).

## Lanes

| Lane | Owns | Model |
|---|---|---|
| S1 LB | L1, L2, L3, tests, openspec entry | Opus |
| S2 harness | agents/*, hooks/*, install-policy.py, settings wiring, ROUTING.md | Opus |
| S3 route CLI | clients/route, routing-table.json, launchd plist, docs | Opus |
| V | one read-only verification per closeout, cross-vendor | Codex Sol xhigh |

All three lanes work in `/Volumes/StudioExt/repos/agent-lb-worktrees/router`
on branch `lane/router`, disjoint files as listed. Commits after every step,
plain messages. Checks: `uv run pytest` (scoped), `uv run ruff check app clients`.

## Deploy

Selective deploy of changed runtime files into `~/.agent-lb/runtime/agent-lb`
with a backup under `runtime/backups/`, hashes verified, per the sync log
practice. Restart in a window when `/api/health` shows in-flight near zero.
Then: `route doctor` green, `/api/pools` real numbers, `route pick implement`
returns `opus-seat`, the pulse fires in a fresh session.

## Acceptance

- `/api/pools` shows a non-null Fable-scoped source for at least one account
  within one pulse cycle of deploy.
- `route pick verify --author-vendor anthropic` never returns an Anthropic seat.
- A dispatch in any session appends to the ledger; its closeout matches by
  prompt hash (`prompt_sha256`), with seat + most-recent-open as the recorded
  fallback. (The SubagentStop payload carries no caller-given name.)
- `route doctor` pointed at an unused port (`AGENT_LB_URL`) exits 1 and
  writes the ALERT; pointed at the live LB with every seat up, exits 0 and
  clears it. (The LB is never stopped for this: the bootout guard and the
  watchdog forbid it.) The health route is `/health`; `/api/health` is 404.
- `route learn` on a fixture ledger with 10 failing closeouts proposes the
  demotion; with 9 it does not.
