# Design

Join 20260910_020000_merge_reset_spool_heads and 20260908_000000_add_guest_session_generation with a new merge node. Follow graph edges rather than date-like filenames. Do not edit either parent or stamp over stored state.

The merge has no schema operations. Upgrade applies the missing parent schema; a merge-only downgrade leaves both parent schemas, exact reset owner/credit bindings, admin and guest credentials, guest-access flag, retention setting and stored generation untouched. A reset-parent database receives generation zero from main's existing migration. Existing nonzero guest generations must remain nonzero.
