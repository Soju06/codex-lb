## Purpose

Trusted-header authentication is safe only when the configured identity field is unambiguous. HTTP permits repeated fields, and append-style reverse proxies can retain a client-supplied occurrence while adding the authenticated identity.

## Decision

The trusted identity parser accepts exactly one configured-header occurrence from a trusted raw peer. Zero fields, a blank singleton, or two or more fields produce no trusted-header identity. Duplicate values are not deduplicated because accepting them would make proxy normalization assumptions part of the application's authentication boundary.

## Failure mode and response

An ambiguous request follows the existing missing-proxy-identity path. Protected dashboard APIs return HTTP 401 with error code `proxy_auth_required`, and no trusted-header admin principal or actor is created. A valid password-authenticated fallback session remains governed by its existing contract.

## Example

A proxy receives `Remote-User: attacker@example.com` and appends `Remote-User: alice@example.com`. The application rejects the request instead of selecting either value. A single `Remote-User: alice@example.com` field continues to authenticate Alice.
