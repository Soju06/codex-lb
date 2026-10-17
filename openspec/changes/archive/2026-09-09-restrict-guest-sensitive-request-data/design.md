## Context

Dashboard authentication currently distinguishes `admin` and `guest`, but every authenticated GET route shares the same broad read gate. Conversation archives contain full request and response bodies, while request-log rows expose client IP, full User-Agent, conversation ID, and the archive lookup ID. Guests still need the non-identifying operational fields and aggregates that make the read-only dashboard useful.

## Goals / Non-Goals

**Goals:**

- Deny every conversation-archive API request unless the dashboard principal is an admin.
- Preserve request-log rows and aggregate metrics for guests while replacing raw identifying request metadata with null values.
- Deny guest use of the dedicated conversation-ID filter so redacted conversation membership and aggregates cannot be queried.
- Keep the admin API and request-detail experience unchanged.
- Avoid presenting archive controls or redacted identifying fields in the guest UI.

**Non-Goals:**

- Changing persistence, retention, archive creation, or proxy routing.
- Redacting low-cardinality `useragentGroup`, request metrics, status, model, token, latency, or cost fields.
- Redesigning the wider dashboard guest-access model.

## Decisions

### Use an explicit admin dependency for sensitive read routes

Add `require_dashboard_admin_access` beside the existing dashboard session and write dependencies. It validates the session, then rejects non-admin principals with a stable `admin_access_required` permission error. The conversation-archive router uses this dependency for all endpoints.

Using the write-access dependency was rejected because archive reads are not mutations and its `read_only_access` message would describe the failure incorrectly.

### Redact at the request-log response boundary and exclude sensitive search branches

The request-log endpoint passes the resolved principal into the service as an explicit `include_sensitive_metadata` decision. The mapper emits null for `clientIp`, full `useragent`, `conversationId`, and `archiveRequestId` for guests while retaining persisted values and all non-sensitive fields.

The service threads the same decision into repository filter construction. Guest text search excludes the `client_ip` predicate so redacted values cannot be reconstructed as a membership oracle; admin search retains the existing client-IP behavior. The role decision is also part of the short-lived filtered-count cache key so an admin result cannot leak through a guest count lookup.

Persistence remains unchanged. Keeping the role decision explicit across filter construction and response mapping provides typed enforcement points while preserving non-sensitive filters and aggregates for both roles.

### Fail closed when a guest supplies the dedicated conversation filter

The request-log API rejects a guest request that supplies `conversation_id` with HTTP 403 and the stable `admin_access_required` error code before querying the request-log service. Admins retain the existing filter, matching rows, request count, and aggregated cost response.

Ignoring the guest parameter was rejected because it would silently return a broader dataset than requested. Passing it through was rejected because the filtered row count and conversation aggregate would disclose whether a redacted conversation identifier exists.

### Hide sensitive request-detail UI by role

The request-detail component reads the authenticated dashboard role. Admins retain the existing User Agent, Client IP, Conversation ID, and archive panel. Guests do not mount or render those elements. Backend redaction remains authoritative; the UI change prevents misleading placeholders and avoidable denied archive requests.

## Risks / Trade-offs

- [Risk] A future request-log identifying field could be added without joining the redaction set. → Keep the sensitive-field list explicit in the role-aware mapper test and OpenSpec requirement.
- [Risk] A redacted request-log field could remain searchable and become a membership oracle. → Pass the role decision into filter construction and test guest and admin search behavior at the API boundary.
- [Risk] A dedicated identifying filter could expose membership or aggregates even when row fields are redacted. → Reject guest `conversation_id` filtering at the API boundary and cover both roles in a public API regression test.
- [Risk] Client-side role state could be stale. → Backend response redaction and archive authorization are authoritative and do not rely on the UI.
- [Trade-off] Guests lose conversation drill-down and archive inspection. → They retain request rows, model/status/token/cost/latency fields, `useragentGroup`, and aggregate dashboard/report statistics.
