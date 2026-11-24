# Context: request-log upstream facet

## Purpose

Let operators isolate request-log traffic by where it was routed: the built-in subscription
pool, or one configured model source, including sources that have since been deleted.

## Decisions

- **Identity is the stable source ID.** `model_source_kind` is display metadata, never filter
  identity. Renamed sources keep their option and filter value; only the label changes.
- **`subscription_pool` means `model_source_id IS NULL`.** It is the subscription route family,
  including failures recorded before an account was selected. It does not claim that an
  account served the request, so it is not derived from `account_id`.
- **Options come from observed logs, not from `model_sources`.** A configured source with no
  traffic in the current window has no option; a deleted source with traffic keeps one.
- **Deleted sources** return `displayName: null`, `isDeleted: true`, and the
  `model_source_kind` persisted on their request logs. The dashboard renders a localized
  `Deleted model source · <id>` label.
- **Duplicate names** are disambiguated in the dashboard as `<name> · <id>`.
- **No secrets.** Label resolution reads only `model_sources.id`, `name`, and `kind`; base URLs
  and encrypted API keys are never selected.
- **Counts.** The demand rollup has no `model_source_id` dimension, so an active upstream filter
  counts from raw rows. The selection is part of the 30-second count-cache key so two
  selections never share a total.
- **Facet cost.** With no user filters the facet skip-scans `model_source_id` through
  `idx_logs_model_source_time` and adds one `LIMIT 1` probe for `model_source_id IS NULL`, the
  same bounded contract as the other facets. Filtered requests use bounded `DISTINCT`.
- **Hidden single option.** When only one upstream is observed and none is selected, the
  dashboard hides the control so subscription-only installs keep their current filter bar.

## Failure modes

- An older backend without `upstreams` in the options response: the dashboard parses the
  field as an empty list and hides the control.
- A bookmarked `upstream` value that no longer appears in the options: the filter still
  applies and the control stays visible so it can be cleared.

## Example

```http
GET /api/request-logs/options
```

```json
{
  "upstreams": [
    {"id": "subscription_pool", "type": "subscription_pool", "displayName": null, "modelSourceKind": null, "isDeleted": false},
    {"id": "src_abc", "type": "model_source", "displayName": "Local vLLM", "modelSourceKind": "openai_compatible", "isDeleted": false},
    {"id": "src_old", "type": "model_source", "displayName": null, "modelSourceKind": "openai_compatible", "isDeleted": true}
  ]
}
```

```http
GET /api/request-logs?upstream=subscription_pool&upstream=src_abc
```

returns rows routed through the subscription pool or `src_abc`, with an exact total.

## Out of scope

Reports and overview aggregation by source, a provider discriminator column or migration,
routing and fallback behavior, account-selection diagnostics, and model-source CRUD.
