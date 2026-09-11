## Context

`#1521` made file pins durable hard ownership and bypassed the
process-session soft row. `#1703` then made `thread_header` /
PROMPT_CACHE the current Codex soft mapping. The bypass was not
updated, so a required file owner still enters sticky persist and
upserts thread B→A.

## Goals / Non-Goals

**Goals:**

- File-pinned routing stays on the pin account.
- An existing thread PROMPT_CACHE row is not rewritten.
- Process-session seed remains insert-if-absent.

**Non-Goals:**

- Changing 1011 file-pin reconnect.
- Weakening file-pin fail-closed or hard-owner conflict checks.
- Dashboard or settings changes.

## Decisions

- Null the writable sticky key for both `session_header` and
  `thread_header` in `preferred_owner_sticky_inputs`. Selection then
  takes the unbound required-owner path.
- Keep `legacy_sticky_key` so a conflicting raw process-session owner
  still fail-closes.
- Leave `sticky_seed_key` to the caller so a missing process
  preference can still initialize without writing the thread row.

**Alternative considered:** persist the thread row onto the file
owner so later unpinned turns stay there. Rejected: the file pin is
hard only for this turn; thread locality is a separate soft mapping.

## Risks / Trade-offs

- [Risk] A later unpinned turn on the same thread stays on the
  pre-file account and cannot see the upload. → Mitigation: that is
  the existing unpinned-file compatibility path; the pin still binds
  any turn that references the file.
