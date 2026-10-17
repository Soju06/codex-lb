## Context

`_reconnect_http_bridge_session` promotes `request_state.preferred_account_id`
to a required owner only when the caller sets `require_preferred_account` or
the session is account-neutral. Submit-on-closed recovery calls
`_retry_http_bridge_request_on_fresh_upstream`, which passes
`require_same_account` only for hard keys and never passes
`require_preferred_account`. After upstream `1011`, a soft `prompt_cache`
session therefore sets `skip_same_account`, excludes the file owner, and
allows fallback. The later precreated-recovery path already pins files.

The existing file-pin requirement already says a live pin MUST override
prompt-cache locality. This change closes the reconnect hole rather than
inventing a new ownership model.

## Goals / Non-Goals

**Goals:**

- Soft `1011` reconnect of a file-pinned request keeps the pin account
  required, or fail-closes if that account is excluded or unavailable.
- Movable soft `1011` reconnects without a live file pin still skip the
  closed account.

**Non-Goals:**

- Durable cross-replica pin persistence (open `#1521`).
- Changing hard-session `1011` keep-owner behavior.
- Changing compact or native WebSocket file routing.

## Decisions

- Honor `file_required_preferred_account` inside reconnect owner resolution
  so every reconnect caller is covered, not only submit-on-closed.
- Also pass `require_preferred_account` from
  `_retry_http_bridge_request_on_fresh_upstream` so that path matches the
  already-correct precreated recovery call.
- If the file-required flag is set but `preferred_account_id` is missing,
  use the current session account (the session was already on the pin owner).

**Alternative considered:** only change the one call site. Rejected because
reconnect still ignores `file_required_preferred_account`, so a future
caller can reopen the hole.

## Risks / Trade-offs

- [Risk] A file-pinned request can no longer leave a `1011`-closed soft
  session's account. → Mitigation: that is the required contract; fail closed
  instead of sending the file to another account.
- [Risk] Existing unit tests assert the fresh-upstream retry call shape.
  → Mitigation: update the no-file assertion and add a file-pin assertion.
