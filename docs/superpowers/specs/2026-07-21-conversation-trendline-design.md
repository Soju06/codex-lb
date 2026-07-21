# Conversation Trendline and Count Copy

## Context

The conversation metric added by `d9c069ed` is visible in the dashboard and
reports, but the dashboard stat has `trend: []`. The existing dashboard trend
payload contains request, token, cost, and error-rate series only. Conversation
totals are calculated separately with a distinct, non-empty conversation ID
count, so the total is available but no bucket series is produced.

The same count is also described twice in the UI: the dashboard shows
`{count} distinct` beneath the Conversations stat and the report card shows the
same wording beneath its value. The requested presentation is to show neither
qualifier while retaining the distinct-count semantics in the backend.

The filtered conversation summary is currently rendered as one plain text
string. Its values should be visually formatted as inline code, matching the
requested shape: `The conversation [ID] runs [count] request(s), cost = [cost]`.

The owning OpenSpec change is
`openspec/changes/support-conversation-grouping/`.

## Goals

- Add a conversation-count trendline to the dashboard for every supported
  timeframe and use the existing stat-card sparkline.
- Count each non-empty conversation ID at most once per time bucket, including
  when one conversation uses multiple models or service tiers in that bucket.
- Remove the `distinct` secondary copy from both dashboard and report
  conversation cards without changing their numeric values.
- Render the filtered conversation summary's ID, request count, and cost as
  styled inline-code spans, with no literal backtick characters.
- Preserve existing report-wide and daily distinct conversation counts,
  filtering behavior, card ordering, and CSV behavior.

## Non-goals

- Changing conversation ID detection, persistence, filtering, or request-log
  summaries.
- Changing report daily bucket resolution.
- Adding a new chart component or a new frontend dependency.
- Changing the meaning of the existing dashboard total.

## Design

### Bucket aggregation

Keep `aggregate_by_bucket()` unchanged because its rows are grouped by bucket,
model, and service tier and are also used for model cost totals. Adding a
distinct conversation count to those rows and summing it could overcount a
conversation that appears in more than one model or tier.

Add a separate request-log repository query grouped only by the normalized time
bucket. It will:

1. Use the same database-specific bucket expression and `since` boundary as the
   existing bucket query.
2. Exclude warmup traffic with the existing shared clause.
3. Count `DISTINCT` trimmed, non-empty conversation IDs using the existing
   conversation-ID normalization rule.
4. Return one small aggregate row per populated bucket.

The dashboard repository exposes this query and the dashboard service loads it
alongside the existing bucket rows. Queries must remain sequential on the
shared async session. The usage builder receives the conversation bucket rows,
maps them to the same aligned slots as the other series, and zero-fills missing
slots.

### API contract

Add `conversations: list[TrendPoint]` to `MetricsTrends`. The dashboard response
will always emit a series with the configured bucket count, even when no
conversation IDs exist. Each point contains the bucket timestamp and the
distinct conversation count for that bucket.

`summary.metrics.conversations` remains sourced from the exact timeframe
activity aggregate. It is not computed by summing trend points, because a
conversation spanning multiple buckets must count once in the overview total.

### Frontend presentation

The Conversations stat uses `trendPointsToValues(trends.conversations)`, so the
existing `StatsGrid` renders its sparkline without a new component. Its
secondary metadata is removed.

The report Conversations card keeps its numeric value but has no secondary
`distinct` line. Summary-card secondary content is rendered only when present,
avoiding an empty placeholder for this card. The now-unused conversation-copy
i18n keys are removed from all supported locale files.

The filtered conversation summary uses the existing `Trans` support from
`react-i18next` so translations can preserve sentence order while wrapping the
conversation ID, request count, and cost in styled `<code>` elements. Its
English copy is `The conversation [ID] runs [count] request(s), cost = [cost]`;
active non-conversation filters remain appended as the existing inline suffix.

### Failure behavior

- Empty or whitespace-only IDs do not contribute to a bucket.
- A bucket with no valid IDs produces a zero point.
- A query returning no rows still produces a full zero-filled trend series.
- Existing dashboard and report totals remain authoritative if the trend series
  contains zero values.

## Alternatives considered

1. **Add distinct counts to model/service-tier rows.** Rejected because the
   builder would need to de-duplicate across groups or risk inflated values.
2. **Regroup the existing query by bucket only.** Rejected because it would
   remove the model grouping needed for existing cost aggregation.
3. **Derive the series from report daily rows or hydrated request logs.**
   Rejected because daily rows do not match dashboard bucket resolution and raw
   log hydration is unnecessary work for a small aggregate.

## Verification

- Repository test proves a conversation appearing under multiple models in one
  bucket is counted once and blank IDs are ignored.
- Builder test proves bucket placement and zero-filling for conversation points.
- Dashboard integration test verifies the new series length and distinct values
  while preserving the exact summary total.
- Dashboard utility/component tests verify the Conversations stat consumes the
  series and has no `distinct` metadata.
- Dashboard summary tests verify the ID, request count, and cost render as
  styled inline-code values with `cost =` punctuation and no literal backticks.
- Report summary-card tests verify the value remains and the `distinct` copy is
  absent.
- Focused backend/frontend checks and strict OpenSpec validation pass before
  completion.
