# Exact OpenAI root error-envelope context

## Purpose

Keep locally generated errors representation-compatible across each OpenAI path
family, including its exact root.

## Decision

Extend the centralized fallback predicate to include `/v1` and `/backend-api`
alongside their existing slash-prefixed descendants. The roots remain
unsupported resources returning 404; only their external error representation
changes.

## Constraints

- Do not register routes or redirects at either exact root.
- Do not change authentication, firewall, SPA routing, or non-OpenAI errors.
- Preserve existing status, message, error type, and error code semantics.

## Failure mode

Without exact-root classification, `/v1` returns `{"detail":"Not Found"}`
while `/v1/` returns the documented OpenAI `error` object. Equivalent clients
therefore receive incompatible schemas solely because of one slash.

## Example

`GET /backend-api` and `GET /backend-api/` both return HTTP 404 with
`error.type = invalid_request_error`, `error.code = not_found`, and
`error.message = Not Found`.
