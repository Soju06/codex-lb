# Conversations

The dashboard's **Conversations** view groups request logs by their
`conversation_id`. It is a read-only, derived view: codex-lb does not store a
separate conversation entity.

Conversation functionality turns request-log metadata into an operator view of
recent conversation activity. It identifies related requests, summarizes their
usage, and lets admins inspect model and reasoning-effort breakdowns without
displaying raw prompt or response content.

## How IDs Are Extracted

codex-lb extracts a conversation ID from the inbound request's `User-Agent` and
conversation headers while creating request-log metadata. The value is stored
as the nullable `conversation_id` field and is then used for dashboard and
report aggregation. The extraction does not modify or reject the proxied
request.

| Client | Header lookup order |
| --- | --- |
| OpenCode | `x-parent-session-id`, `x-opencode-session`, `x-session-id`, `x-session-affinity` |
| Codex | `thread-id` |

User-agent and header-name matching is case-insensitive. The first non-empty
header value is selected and surrounding whitespace is removed. Requests from
unsupported clients, or requests without a usable matching header, have no
conversation ID and are not grouped. Empty IDs are also excluded from the
conversation view. The same metadata is carried through normal HTTP,
WebSocket, and supported control or auxiliary request-log paths.

## Dashboard Data

The dashboard overview shows **Active Conversations** for the selected `1d`,
`7d`, or `30d` timeframe, the average requests per conversation, and a
conversation trend alongside the other request, token, and cost metrics.

Open **Dashboard**, use the view selector next to the Requests heading, and
choose **Conversations**. The view supports:

- Activity windows of `1d`, `7d`, or `30d` (the default is `7d`)
- Pagination

The dashboard view does not expose a free-text search control. API clients may
search by conversation ID or user-agent family using the `search` parameter
documented below.

The conversation table shows:

- Last request time and total conversation duration
- Conversation ID
- Representative account and API key, with counts for additional accounts
- Representative model, with counts for additional models
- Request count, total and cached tokens, and total cost

Selecting **Details** opens a dialog with the conversation ID, start and latest
timestamps, account count, total elapsed time, dominant user-agent group, and
per-model/reasoning-effort totals for requests, elapsed time, input tokens,
cached input tokens, output tokens, and cost.

A conversation appears when it has at least one eligible request inside the
selected activity window. Its displayed start time and aggregate totals still
include the conversation's full eligible history. For example, a conversation
that started before the window but was active during it is listed, and its
`firstRequest` can be older than the selected window.

## Reports Data

The Reports page does not list conversation IDs or raw conversation content. It
shows aggregate counts for the selected date range, timezone, and account,
model, and user-agent filters:

- The summary card shows **Active Conversations**, the number of distinct
  non-empty conversation IDs in the report range.
- The daily breakdown includes a sortable **Conversations** column with the
  distinct conversation count for each local report day.
- The daily breakdown CSV export includes the same Conversations value for
  each day.

The summary count is distinct across the whole report range, while a
conversation active on multiple days can appear in multiple daily counts. Do
not add the daily values to reproduce the summary total.

## API

Both endpoints require an authenticated dashboard **admin** principal. Guest
requests receive HTTP `403` with error code `admin_access_required`.

### List conversations

```http
GET /api/conversations?timeframe=7d&search=conv&limit=25&offset=0
```

Query parameters:

- `timeframe` — `1d`, `7d`, or `30d`
- `since` — an explicit ISO 8601 activity-window start; it cannot be used with
  `timeframe` and is capped at a 30-day lookback
- `search` — optional conversation ID or user-agent family search text
- `limit` — page size from `1` to `1000` (the API default is `50`)
- `offset` — zero-based page offset

The response contains `conversations`, `total`, and `hasMore`. Each list entry
includes the conversation ID, first and last request timestamps, request count,
representative account and model information, token totals, cached input
tokens, and total cost.

### Get conversation details

```http
GET /api/conversations/{conversation_id}
```

The detail response includes `conversationId`, `start`, `latest`,
`accountCount`, `totalElapsedTime`, `dominantUseragentGroup`, and
`modelStats`. Each model statistic includes the model and reasoning effort,
request count, elapsed time, input/output/cached token totals, and cost.

Conversations are derived from eligible request-log rows. Empty conversation
IDs and rows excluded by the request-log retention filters do not create a
conversation entry.

---

*Spec: [conversations-api](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/conversations-api)*
