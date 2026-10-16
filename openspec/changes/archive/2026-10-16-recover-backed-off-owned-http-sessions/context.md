## Purpose and scope

This change combines the hard-owner selection fix proposed by cigro in
[#2486](https://github.com/Soju06/codex-lb/pull/2486) with additional native HTTP
route coverage and an explicit OpenSpec contract. It is separate from #2626.

## Reproduction and rationale

An observed thread had available quota, several transient server errors, and a
denied proxy-injected response anchor. The bridge cleared the anchor metadata,
but a later retry still required that account through turn-state ownership.
Its narrowed one-account pool could not use the existing backoff fallback.
Other sessions continued on healthy siblings while this thread returned 502.

The native backend regression fails on main at the resumed HTTP request:
502 instead of 200. With the fix, both HTTP routes recover on the original
owner and a later turn succeeds using the returned turn state. Tests use
synthetic accounts, a real generated API key, isolated SQLite, real selection
and health recording, and stubbed upstream sockets.

## Constraints and example

For account A in transient backoff and healthy account B, an unbound thread
can use B. A turn bound to A may use the hard-owner fallback on A, forwarding
its supplied reasoning and completed tool history unchanged after the old
anchor is cleared. A genuinely quota-exceeded A remains unavailable and its
opaque history is never sent to B. No tool outputs or missing history are
invented by the proxy.

## Limits and upstream integration

The selector implementation is intentionally the same as #2486, not a new
competing fallback. The new contribution is route-level recovery/negative
coverage and the normative contract. If #2486 merges first, update this branch
against main and retain only the new coverage and documentation.

The change does not fix the initial generic upstream error, the native error
delivery issue tracked by #2493, unavailable-account migration, or automatic
reviewer authentication. No production container or client configuration is
modified by these tests.
