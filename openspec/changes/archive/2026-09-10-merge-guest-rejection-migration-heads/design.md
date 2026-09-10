## Context

The published rejection/spool merge and the guest-session migration both depend on spool retention. Current main `d6a7ca662` combines with PR `358f61edc` without text conflicts, but public migration fails with multiple heads.

## Goals / Non-Goals

Restore a single head while retaining all published migration history. Guest authorization and ERR recovery policy are outside the repair.

## Decisions

Append `20260910_180000_merge_guest_rejection_heads` with both existing heads as parents. Do not amend or reparent the already published join. Keep the earlier rejection/spool regression valid for the new guest column, whose specified default is zero.

## Risks / Trade-offs

A subsequent migration on an independent branch may require another join. Recheck current main before publication and handoff. Merge-only downgrades retain both histories; they do not revoke the guest migration or remove rejection fields.

## Migration Plan

Use the normal upgrade CLI. For example, a database with guest generation 9 retains that value while acquiring rejection fields with their original defaults. A database with a probe claim and saved retention gains guest generation zero. Verify these populated states on SQLite and PostgreSQL before delivery.
