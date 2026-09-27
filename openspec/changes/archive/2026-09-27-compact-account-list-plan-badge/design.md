## Context

Existing summaries already contain recorded subscription timestamps. AccountClockProvider supplies a shared minute clock; AccountSubscription can expose a small presentation without separate per-account requests or timers.

## Goals / Non-Goals

**Goals:** Exact compact day/hour presentation, preserve original charts, and materially reduce List row height.

**Non-Goals:** Change the full Grid/detail content, API metadata, routing or management behavior.

## Decisions

- Compact subscription presentation uses whole days and remaining whole hours, each padded to two digits, with literal d/h suffixes (e.g. 05d 08h). A positive period below an hour is 00d 00h; elapsed is a separate short label. The title and accessible label retain snapshot meaning, recorded end date and last check.
- Only original Detail selector items opt into the new status-underlabel; Grid headers keep their full subscription panel without a duplicate short label.
- List uses four aligned columns at desktop width: identity, plan/status, remaining plan, and quota. Primary and weekly windows sit side by side to avoid increasing row height. On mobile these groups wrap inside the viewport.
- List omits request totals, cost, credentials, reset/purchased credits, routing/warm-up details and long subscription dates. Existing selected details and Grid retain full content. Status recovery reasons remain available as a badge tooltip.
- Keep shared filters, pagination, selection and existing quota preference/monthly-only handling.

## Risks / Trade-offs

- Compact time has hour precision, so a positive period below an hour reads 00d 00h; the recorded deadline remains accessible in the tooltip and details.
- Missing metadata shows No data and elapsed snapshots show Elapsed, never a fabricated duration or disabled account state.
- Detail selector width is narrow; keeping the duration short avoids squeezing account identity with long text.
