# Design

## Classification

Recognize only an `invalid_request_error` whose normalized message exactly
matches:

```text
The '<requested model>' model is not supported when using Codex with a ChatGPT account.
```

The quoted slug must equal the request's model. Internally classify this as
`account_model_unsupported`; do not add the generic upstream code
`invalid_request_error` to retry sets.

## Replay boundary

Replay at most once and only before upstream acceptance: no response id, no
nonterminal `response.*` event, no downstream sequence/output, and no other
pending request sharing the upstream socket. Initial turns may move accounts.
A continuation may move only when the proxy already retained and verified a
self-contained fresh body without the injected owner anchor. Account-scoped
file references and other hard owner bindings never move.

## Routing and health

Exclude the rejecting account in request-local state, drop its per-account
response-create lease, and select again with the same model and service tier.
Existing model-catalog filtering therefore limits the replacement to an
account currently advertising the requested model. The rejection is evidence
of stale per-account routing metadata, not general account failure, so it must
not update error health or deactivate the account.

## Terminal behavior

If replacement selection or connection cannot find a compatible account, emit
the original upstream status and error envelope. Once a replacement connection
has been established, later failures belong to that replacement attempt and
follow the normal error path.
