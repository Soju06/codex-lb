# `route` — the routing CLI

`clients/route` is contract C4 of [the router program](router-program.md). It answers one
question — *which seat should this task go to right now* — from three sources of truth:
the routing table (policy), pool headroom from the LB (capacity), and `routing-state.json`
(what is actually up). It also records what happened and turns that record into policy.

Python 3 stdlib only, no dependencies, executable in place.

---

## Commands

### `route pools [--json]`

Pool headroom, per contract C1. Reads `GET /api/pools` from the LB.

Until S1 lands that endpoint the CLI degrades instead of failing: on a 404 it derives an
approximation from `GET /api/accounts` and marks every pool `"source": "accounts_fallback"`
(and the document the same). The approximation counts an account as usable when its status
is `active`; `headroomPercent` is the best usable account's `usage.secondaryRemainingPercent`,
`aggregateRemainingPercent` is the mean across usable accounts, and `resetAt` is the earliest
`resetAtSecondary`. Anthropic accounts land in `anthropic-general`, and additionally in
`anthropic-fable` when `fableEligible` is true — a heuristic pool, not a scoped marker.

Status thresholds are C1's: `ok` at headroom ≥ 25, `low` at 5–25, `exhausted` below 5.

Exit 1 when neither endpoint answers.

### `route pick <class> [--author-vendor V] [--json]`

The first chain entry for `<class>` that is actually routable. Prints `{seat, model, pool,
reason, fallbacks}`; `--json` gives the raw object, which is what a hook or another script
should read.

Filtering, in order:

1. **Overrides.** Every `overrides` entry matching this class moves its chain entry to the
   end of the chain (a demotion, never a deletion).
2. **Cross-vendor.** When the class sets `"cross_vendor": true`, entries whose vendor equals
   `--author-vendor` are excluded. The vendor is the entry's own `vendor` field when it has
   one, else the first segment of its pool id (`openai-codex` → `openai`). Omitting
   `--author-vendor` on a cross-vendor class is a usage error: **exit 2**, because silently
   verifying a diff on its author's vendor is the failure this rule exists to prevent.
3. **Seat health.** A seat recorded `"ok": false` in `~/.claude/routing-state.json` is
   skipped. A missing file, or a seat with no record, counts as up — the doctor takes seats
   out, it does not have to vouch for them.
4. **Pool capacity.** A seat whose model maps to an `exhausted` pool is skipped. Pool status
   comes from a 3-second `route pools` read, falling back to the `pools` block the doctor
   last wrote. Unknown pool status never blocks a pick; it is named in `reason`.

A class marked `{"driver": true}` (that is, `plan`) returns seat `driver`: it stays on the
Fable driver session and spawns nothing.

Exit 2 when nothing is routable, with the skip reasons on stderr.

### `route doctor [--write]`

Probes, in order, inside a 55-second budget:

| probe | what it does | timeout |
|---|---|---|
| `lb-health` | `GET /api/health`, falling back to `/health` on a 404 (the shipped LB serves `/health`) | 4s |
| `lb-pools` | `GET /api/pools`, or the `/api/accounts` approximation | 5s |
| `cursor-agent` | `cursor-agent status` | 10s |
| `codex-companion` | `node ~/.agent-lb/plugins/codex-plugin-cc/plugins/codex/scripts/codex-companion.mjs status` | 15s |
| `model:<m>` | one minimal `POST /v1/messages` (`max_tokens: 1`, prompt `ok`) per distinct non-Fable Claude model in the table | 10s each |

The model probes are real LB requests. On loopback the LB's dashboard and proxy paths accept
an unauthenticated local request (`validate_dashboard_session` admits local clients when no
dashboard password is set), so the doctor holds no credential and prints none. If
`/v1/messages` ever answers 401/403, the probe falls back to checking `/api/models` is present
and records `"method": "api_models_presence"` on that seat, so the state file never claims a
round trip it did not make. A 429 is a failure, not a pass: it means no account is selectable
for that model right now.

Seat health is derived from the probes: `cursor-seat` follows `cursor-agent` (it forwards
whatever model id it carries), `gpt-*` models follow `codex-companion`, probed Claude models
follow their own probe, and Fable-pool models follow `lb-health`.

`--write` writes `~/.claude/routing-state.json`:

```json
{"ts":"…","seats":{"opus-seat":{"ok":false,"latency_ms":1330,"error":"HTTP 429 (…)"}},
 "pools":{…C1 document…},"probes":[…],"auth":"loopback_unauthenticated","elapsed_s":3.8}
```

and, when any probe failed, `~/.claude/routing-ALERT` (plain text, like `~/.jev/ALERT`),
which it deletes on a clean run. Without `--write` it prints the same state and touches
nothing. **Exit 1 when any probe failed**, with or without `--write`.

### `route report [--days N]`

Joins the dispatch ledger (contract C2, `~/.claude/logs/dispatch.jsonl`) with LB session
analytics and prints Markdown: per class × seat × model, the dispatch count, denied count,
closeout count, ok-rate, median duration and cost. Default window 7 days.

Closeouts are matched to dispatches by `(session_id, name)`, oldest unmatched first, so a
closeout inherits the dispatch's `task_class` and `model` (the SubagentStop hook does not
know the model). Cost comes from `GET /api/sessions/<id>/analytics` (`session.costUsd`) for
up to 20 distinct sessions. A session's cost is the whole session's spend, so a session that
appears in several rows is counted in each; the report says so under the table.

### `route learn [--apply]`

The rule from C4, and the only way `overrides` is ever written: a `(class, seat, model)` with
**n ≥ 10** closeouts in the window and an **ok-rate < 0.6** is proposed for demotion to the end
of its chain. Window defaults to 14 days (`--days`); the thresholds are `--min-closeouts` and
`--ok-floor` for experiments, not for making a bad seat look good.

Without `--apply` it prints the proposed diff. With `--apply` it appends
`{class, demote:{seat,model}, reason, evidence, ts}` to the table's `overrides` and rewrites it
atomically. Re-running is a no-op: a demotion already recorded is not written twice.

---

## Files

| path | what | written by |
|---|---|---|
| `clients/route` | the CLI | this lane |
| `config/coding-agents/routing-table.json` | contract C3: classes, chains, pools, overrides | this lane; `overrides` by `route learn --apply` |
| `~/.agent-lb/managed/coding-agents/routing-table.json` | the installed table the CLI reads by default | `install-policy.py` |
| `~/.claude/routing-state.json` | contract C4 state: seats, pools, probes | `route doctor --write` |
| `~/.claude/routing-ALERT` | degraded-routing alert | `route doctor --write` |
| `~/.claude/logs/dispatch.jsonl` | contract C2 ledger | `seat-guard.py`, `subagent-closeout.py` |

### Environment

| var | default | use |
|---|---|---|
| `AGENT_LB_URL` | `http://127.0.0.1:2455` | LB base URL |
| `ROUTE_TABLE` | installed table, else the repo copy beside the script | routing table path |
| `ROUTE_STATE` | `~/.claude/routing-state.json` | state path |
| `ROUTE_ALERT` | `~/.claude/routing-ALERT` | alert path |
| `ROUTE_LEDGER` | `~/.claude/logs/dispatch.jsonl` | ledger path |
| `ROUTE_CURSOR_CMD` | `cursor-agent status` | cursor probe command |
| `ROUTE_CODEX_CMD` | `node <codex-companion.mjs> status` | codex probe command |
| `ROUTE_FIXTURE_DIR` | unset | **test only.** Serves HTTP reads from files in that directory instead of the network: `/api/pools` reads `api_pools.json`, `/api/accounts` reads `api_accounts.json`, a missing file is a 404. Never set it outside `tests/unit/test_route_cli.py`. |

---

## Install

`install-policy.py` (owned by the harness lane) installs the CLI at `~/.agent-lb/bin/route`
and the table at `~/.agent-lb/managed/coding-agents/routing-table.json`, and loads the doctor
as a launchd job — label `com.aneyman.route-doctor`, every 30 minutes:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>com.aneyman.route-doctor</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/aneyman/.agent-lb/bin/route</string>
    <string>doctor</string>
    <string>--write</string>
  </array>
  <key>StartInterval</key>    <integer>1800</integer>
  <key>RunAtLoad</key>        <true/>
  <key>StandardOutPath</key>  <string>/Users/aneyman/.agent-lb/logs/route-doctor.log</string>
  <key>StandardErrorPath</key><string>/Users/aneyman/.agent-lb/logs/route-doctor.err</string>
</dict>
</plist>
```

Installed at `~/Library/LaunchAgents/com.aneyman.route-doctor.plist`, loaded with
`launchctl bootstrap gui/$(id -u)`. The doctor exits 1 while routing is degraded, which is
expected and is not a reason for launchd to stop running it.

---

## Verify this page

From the repo root:

```bash
uv run pytest tests/unit/test_route_cli.py -q            # 15 tests, all five C4 behaviours
ROUTE_TABLE=config/coding-agents/routing-table.json clients/route pools
ROUTE_TABLE=config/coding-agents/routing-table.json clients/route pick implement
ROUTE_TABLE=config/coding-agents/routing-table.json clients/route pick verify --author-vendor anthropic
ROUTE_TABLE=config/coding-agents/routing-table.json clients/route doctor        # exit 1 while a seat is down
```

`pick verify --author-vendor anthropic` must never return an Anthropic seat; that is the
acceptance line the program is holding this lane to.
