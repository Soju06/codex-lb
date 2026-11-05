## Why

Request logs already persist `model_source_id` and `model_source_kind` for traffic served
by configured model sources (#1129), and `GET /api/request-logs` returns both fields, but the
dashboard neither shows nor filters by them. Model-source rows read as `Unassigned` in the
Account column, two sources that serve the same model slug cannot be told apart, and traffic
from a deleted source cannot be isolated even though its stable source ID survives on every
row (#1870).

## What Changes

- `GET /api/request-logs` accepts repeated `upstream` parameters. `subscription_pool` matches
  rows with no model source; any other value matches that exact `model_source_id`. Values are
  OR'd with each other and AND'd with the existing filters. Requests without the parameter are
  unchanged.
- An active upstream filter takes the exact raw count path instead of the demand rollup (which
  has no source dimension), and the selection is part of the listing-count cache identity.
- `GET /api/request-logs/options` returns an `upstreams` facet built from observed,
  non-soft-deleted request logs. The facet ignores its own selection. Labels resolve from
  `model_sources` (name and kind only); a deleted source keeps its option with the kind
  persisted on its logs.
- The dashboard Request Logs view gains a URL-backed Upstream multi-select, hidden when only
  one upstream is observed and none is selected, a compact model-source badge on source-routed
  rows, and the full upstream ID and kind in request details. Labels are localized in `en`,
  `ko`, and `zh-CN`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: request-log filtering by upstream and model-source attribution in
  the request-log table.
- `query-caching`: upstream filters participate in listing-count cache identity and bypass the
  demand-rollup count; the unfiltered upstream facet uses indexed probes.

## Impact

Request-log API, service, and repository; dashboard request-log filters and table; three
locale files. No migration (`model_source_id`, `model_source_kind`, and
`idx_logs_model_source_time` already exist), no new setting, dependency, navigation item,
README section, or setup step. Routing, fallback, accounting, reports, and model-source CRUD
are unchanged.
