## Summary

This change adds a user-selectable activity timeframe to the dashboard overview while keeping quota-window visualization unchanged. The overview API becomes explicitly timeframe-aware and returns a clean, window-neutral response contract.

The clean contract is the main design decision in this change. The existing field names encode a 7-day assumption and should not survive the feature.

## Goals

- Support overview activity horizons of `1d`, `7d`, and `30d`
- Keep the response self-describing so the frontend does not infer timeframe from field names
- Preserve stable, readable trend density per timeframe
- Keep request-log filters independent from overview timeframe state
- Keep quota donuts and depletion calculations based on actual quota windows

## Non-Goals

- Do not make the primary/secondary quota windows user-selectable
- Do not merge the overview timeframe control with the request-log timeframe filter
- Do not preserve legacy overview fields for backward compatibility

## API Contract

### Request

`GET /api/dashboard/overview?timeframe=<key>`

Supported values:

- `1d`
- `7d`
- `30d`

The `timeframe` parameter is validated strictly. Unsupported values should be rejected by the API layer rather than silently coerced.

If the client omits `timeframe`, the server defaults to `7d`.

### Response

The locked response shape is:

```json
{
  "lastSyncAt": "2026-04-03T10:00:00Z",
  "timeframe": {
    "key": "7d",
    "windowMinutes": 10080,
    "bucketSeconds": 21600,
    "bucketCount": 28
  },
  "accounts": [],
  "summary": {
    "primaryWindow": {
      "remainingPercent": 63.5,
      "capacityCredits": 225,
      "remainingCredits": 142.875,
      "resetAt": "2026-04-03T10:30:00Z",
      "windowMinutes": 300
    },
    "secondaryWindow": {
      "remainingPercent": 55.2,
      "capacityCredits": 7560,
      "remainingCredits": 4173.12,
      "resetAt": "2026-04-10T10:00:00Z",
      "windowMinutes": 10080
    },
    "cost": {
      "currency": "USD",
      "totalUsd": 486.72
    },
    "metrics": {
      "requests": 22480,
      "tokens": 1918000000,
      "cachedInputTokens": 382000000,
      "errorRate": 0.008,
      "errorCount": 180,
      "topError": "rate_limit_exceeded"
    }
  },
  "windows": {
    "primary": {
      "windowKey": "primary",
      "windowMinutes": 300,
      "accounts": []
    },
    "secondary": {
      "windowKey": "secondary",
      "windowMinutes": 10080,
      "accounts": []
    }
  },
  "trends": {
    "requests": [],
    "tokens": [],
    "cost": [],
    "errorRate": []
  },
  "depletionPrimary": null,
  "depletionSecondary": null
}
```

### Removed Legacy Fields

The following fields are explicitly removed from `GET /api/dashboard/overview`:

- `summary.cost.totalUsd7d`
- `summary.metrics.requests7d`
- `summary.metrics.tokensSecondaryWindow`
- `summary.metrics.cachedTokensSecondaryWindow`
- `summary.metrics.errorRate7d`

## Timeframe Mapping

The response exposes a stable trend density by timeframe:

- `1d` -> `windowMinutes = 1440`, `bucketSeconds = 3600`, `bucketCount = 24`
- `7d` -> `windowMinutes = 10080`, `bucketSeconds = 21600`, `bucketCount = 28`
- `30d` -> `windowMinutes = 43200`, `bucketSeconds = 86400`, `bucketCount = 30`

The `trends.*` arrays must contain exactly `bucketCount` points.

## UI Behavior

- The dashboard header gets a dedicated overview timeframe selector.
- Changing the overview timeframe refetches only the overview query.
- Changing request-log filters, including request-log timeframe, refetches only request-log queries.
- The stats card labels and summaries derive from `response.timeframe`, not hard-coded `7d` text.

## Quota Window Behavior

The selected overview timeframe changes only request-log-derived activity totals and trend charts.

The following remain tied to real quota windows:

- `summary.primaryWindow`
- `summary.secondaryWindow`
- `windows.primary`
- `windows.secondary`
- `depletionPrimary`
- `depletionSecondary`

This preserves the semantic distinction between:

- quota health: how much allowance remains in the current primary/secondary quota windows
- recent activity: what the proxy handled over the selected overview horizon

## Query And Cache Behavior

The frontend overview query key must include the selected timeframe so `1d`, `7d`, and `30d` results do not overwrite one another in cache. Account mutations should still invalidate all dashboard overview variants via the shared key prefix.

## Migration Notes

- This is a deliberate breaking response cleanup.
- The frontend, mocks, tests, and screenshot fixtures must migrate in the same change as the backend response update.
- No compatibility shim should be added unless a downstream consumer outside the React dashboard is later discovered.
