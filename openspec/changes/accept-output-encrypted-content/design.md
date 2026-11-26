## Context

The native request model owns the include allowlist, and the public V1 model delegates to it during conversion.

## Goals / Non-Goals

Accept the new selector without weakening validation. Do not change transport, encryption, account ownership, or replay policies.

## Decisions

Add exactly one supported selector and cover both request models, including rejection of an unknown selector.

## Risks / Trade-offs

Request-model tests demonstrate validation and serialization only. They do not establish upstream cryptographic behavior or live transport compatibility.
