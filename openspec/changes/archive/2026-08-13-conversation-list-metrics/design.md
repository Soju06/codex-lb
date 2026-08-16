# Conversation list metrics

## Scope

This change extends the existing conversation-list aggregation contract owned
by `frontend-architecture`. It does not change request-log capture or the
conversation-ID detection rules.

## Backend aggregation

The existing eligible-row scope MUST remain in force: rows are grouped only
when the normalized conversation ID is non-empty, `warmup` and `limit_warmup`
request kinds are excluded, and eligible rows satisfy `deleted_at IS NULL`.
Soft-deleted rows have `deleted_at IS NOT NULL` and are excluded. The list
aggregation SHALL use `COUNT(*)` over those eligible rows for the request
total, `MIN(requested_at)` for the first request, and the existing latest
eligible `requested_at` for the last request.

## Frontend presentation

The frontend SHALL calculate the elapsed wall-clock duration as
`lastRequest - firstRequest`. It SHALL display `0s` for zero duration, seconds
for durations under one minute, `xm ys` for durations under one hour, `xh ym`
for durations under one day, and `xd yh` for durations of at least one day.
Conversation-ID cells SHALL be top-aligned so wrapped IDs remain readable.

The exact table column order is: Last request, Lasted, Conversation, Accounts,
API key, Models, Requests, Tokens, Cost, Details.
