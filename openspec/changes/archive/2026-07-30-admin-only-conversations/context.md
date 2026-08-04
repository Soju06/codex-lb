# Admin-Only Conversations Context

## Decision

Conversation list and detail reads are sensitive dashboard data. The server
must use the existing `require_dashboard_admin_access` dependency at the router
boundary, while the SPA must hide the selector item and avoid mounting the
conversation query for guests. The UI check is defense in depth; server
authorization remains authoritative.

## Deep Links

A guest URL containing `view=conversations` is normalized to Request Logs. It
must not render a conversation loading state, error state, table, or detail
dialog, and it must not issue a conversation API request.

## Non-Goals

The existing `/api/conversation-archive/*` admin-only routes, conversation
schemas, aggregation semantics, pagination, and request-log redaction are not
changed.
