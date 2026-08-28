# Automations Context

## Scope

First automation type: scheduled daily ping job.

- Trigger: once per day at configured local time
- Target: selected account set + model
- Action: send lightweight ping request through existing proxy flow
- Observability: store run outcome + error details

## API Sketch (MVP)

`GET /api/automations`

```json
{
  "items": [
    {
      "id": "job_123",
      "name": "Daily readiness ping",
      "enabled": true,
      "schedule": {
        "type": "daily",
        "time": "05:00",
        "timezone": "Europe/Warsaw"
      },
      "model": "gpt-5.3-codex",
      "prompt": "ping",
      "accountIds": ["acc_a", "acc_b"],
      "nextRunAt": "2026-04-16T03:00:00Z",
      "lastRun": {
        "trigger": "scheduled",
        "status": "success",
        "accountId": "acc_b",
        "finishedAt": "2026-04-15T03:00:03Z"
      }
    }
  ]
}
```

`POST /api/automations`

```json
{
  "name": "Daily readiness ping",
  "enabled": true,
  "schedule": {
    "type": "daily",
    "time": "05:00",
    "timezone": "Europe/Warsaw"
  },
  "model": "gpt-5.3-codex",
  "prompt": "ping",
  "accountIds": ["acc_a", "acc_b"]
}
```

`GET /api/automations/{id}/runs?limit=20`

```json
{
  "items": [
    {
      "id": "run_987",
      "scheduledFor": "2026-04-15T03:00:00Z",
      "startedAt": "2026-04-15T03:00:00Z",
      "finishedAt": "2026-04-15T03:00:03Z",
      "trigger": "scheduled",
      "status": "failed",
      "accountId": "acc_a",
      "errorCode": "usage_limit_reached",
      "errorMessage": "The usage limit has been reached"
    }
  ]
}
```

Recommended endpoints:

- `GET /api/automations`
- `POST /api/automations`
- `PATCH /api/automations/{id}`
- `DELETE /api/automations/{id}`
- `POST /api/automations/{id}/run-now`
- `GET /api/automations/{id}/runs`

Validation contract:

- `name`: non-empty, trimmed
- `schedule.type`: `daily` only (MVP)
- `schedule.time`: `HH:MM` (24-hour)
- `schedule.timezone`: valid IANA TZ ID
- `schedule.days`: unique subset of `mon..sun`, at least one item
- `schedule.thresholdMinutes`: integer `0..240`
- `model`: non-empty
- `reasoningEffort`: optional (`minimal|low|medium|high|xhigh`)
- `accountIds`: unique list; empty means "all available accounts"
- `prompt`: optional, defaults to `ping`

Dashboard error codes:

- `invalid_account_ids`
- `invalid_schedule_time`
- `invalid_schedule_timezone`
- `automation_not_found`

## UI Sketch (MVP)

New top-level tab: `Automations`

- Job table columns:
  - name
  - enabled
  - schedule (time + timezone)
  - model
  - prompt
  - accounts count
  - next run
  - last run status
- Actions:
  - create
  - edit
  - enable/disable
  - delete
  - run now
- Detail drawer/panel:
  - recent run history
  - full error details for failed runs
  - trigger (`manual`/`scheduled`) and terminal account used

## Scheduling Notes

- Persist schedule timezone explicitly (IANA string).
- Compute next-run in UTC from local schedule, including DST transitions.
- Use leader election + atomic claiming for multi-replica safety.
- Guarantee one execution per `(job_id, scheduled_for)` slot.
- Store deterministic run claim key per slot (`job_id + slot_key`) to enforce uniqueness.
- Persist one cycle snapshot (`automation_run_cycles` + `automation_run_cycle_accounts`) before dispatching accounts.
- Freeze both the eligible account set and each account's planned dispatch time for the lifetime of that cycle.
- Creating or editing a job after today's local slot has already passed does not backfill a new same-day cycle; only the next eligible future local slot is started automatically.
- If a cycle for today's slot already exists, later job edits do not cancel its remaining pending dispatches.
- On cold restart, execute at most the latest eligible due slot instead of replaying historical backlog.

## Safety Notes

- Automation pings must not mutate durable sticky-thread/codex-session continuity for end-user traffic.
- Failover should remain scoped to accounts configured on the job; no global account pool spillover.
- Automation runs should bypass sticky session writes entirely and call upstream compact flow directly.
