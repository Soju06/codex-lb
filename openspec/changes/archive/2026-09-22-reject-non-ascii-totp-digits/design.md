# Design

## Context

See [proposal.md](proposal.md). The shared verifier feeds setup, login, and
security mutations; callers already translate an invalid result into HTTP 400.

## Goals / Non-Goals

Constrain normalization to ASCII while preserving replay and time-window rules.
Changing authentication policy or adding Unicode numeral transliteration is out
of scope.

## Decisions

Replace `isdigit()` with membership in `0123456789`. Catching `TypeError` in each
caller would leave the invalid internal representation intact. Transliteration
would introduce new accepted credentials rather than preserve current behavior.

## Risks / Trade-offs

Existing normalization ignores formatting, so non-ASCII characters are discarded
like other non-digit characters. Regression tests cover Unicode-only and mixed
six-character codes, formatted ASCII codes, and unchanged replay counters.
