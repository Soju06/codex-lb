## Context

Current #875 already has `reauth_required` as the correct state for bad local
credentials or expired upstream sessions. That means newly recognized
credential/session codes should not deactivate an upstream account; they should
remove it from routing until an operator re-authenticates it.

#875 also uses proxy pools and account bindings, not #804's direct per-account
SOCKS form. Import-time proxy validation is therefore out of scope.

## Decisions

1. Map `app_session_terminated` to `reauth_required`.

   The code is an upstream session/credential failure, not proof that the
   upstream account was disabled, suspended, or deleted.

2. Refresh one account per scheduler slice.

   Background usage refresh still covers all accounts over one configured
   interval, but it spreads the work across slices to avoid synchronized
   refresh bursts.

3. Keep routing-unavailable state in process-local memory.

   This complements persisted account status. It specifically protects stale
   long-lived bridge sessions whose in-memory `Account` object may predate a
   state transition.

4. Store one server-owned Codex installation id per account.

   The id is generated server-side, backfilled for existing accounts, and
   injected into upstream Codex metadata. Client-supplied installation ids are
   stripped so callers cannot cross-contaminate accounts.

## Constraints

- The migration must chain onto the current #875 Alembic head.
- Metadata injection must use the current split proxy modules rather than the
  old monolithic #804 proxy service patch.
