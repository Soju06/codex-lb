# Context: quota cleanup during response-header preparation

## Purpose and scope

This change closes the short ownership gap between API-key quota admission and
route-specific settlement ownership. Normative behavior lives in
[`specs/api-keys/spec.md`](./specs/api-keys/spec.md).

## Decisions and constraints

Header calculation remains after admission so successful responses continue to
reflect the committed reservation. The route keeps cleanup ownership only
until headers are ready; stream or service settlement then continues unchanged.
Borrowed reservations remain owned by their origin.

## Failure modes

A database, cache, or calculation error while building rate-limit headers can
occur after quota has been reserved but before upstream work begins. The owned
reservation must be released once before that error propagates. A separate
failure of the release persistence itself is logged without replacing the
header error and continues to use the repository's existing stale-recovery
contract.

## Concrete example

For a limited `POST /v1/responses` request with `stream: false`, admission first
commits reservation `R`. If rate-limit header construction then raises, the
route releases `R` exactly once, starts no upstream stream, and preserves the
header failure for normal error handling.
