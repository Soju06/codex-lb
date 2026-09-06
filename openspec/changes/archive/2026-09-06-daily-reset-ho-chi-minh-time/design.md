## Context

See [proposal.md](proposal.md) for motivation. API-key limits use naive UTC timestamps. Daily reset calculation is shared by creation, explicit reset, lazy expiry, and background expiry; the scheduler separately aligns existing rows ten minutes before midnight.

## Goals / Non-Goals

- Use one boundary calculation for the daily reset and alignment timer.
- Preserve UTC storage, existing task ownership, leader gating, and other window durations.
- No host timezone change, new setting, migration, or production deployment is included.

## Decisions

Use the operator-requested fixed UTC+7 offset for Asia/Ho_Chi_Minh in daily boundary calculation. Shift the UTC clock to the local calendar, select the next local midnight, and shift back to UTC. This needs no timezone database dependency or process timezone mutation.

Derive the next alignment time from the shared daily boundary minus ten minutes, advancing one day if that time has passed. This avoids a separate hardcoded UTC schedule drifting from the reset boundary.

## Risks / Trade-offs

- Existing future reset timestamps converge at the next scheduled alignment; expired rows converge through existing expiry paths. A deployment after the alignment time can leave legacy rows until the following pass.
- The alignment pass only moves timestamps; counters clear on the existing lazy expiry or hourly fallback. This change does not add a new midnight sweep.
- Reservations retain the existing reset-epoch compare-and-set settlement behavior because the alignment repository operation is unchanged.

## Migration Plan

No schema or manual data update is needed. After deployment, verify the alignment log at 16:50 UTC and daily reset timestamps at 17:00 UTC. For example, a key created at 2026-09-06 16:59 UTC expires at 2026-09-06 17:00 UTC, one minute later.
