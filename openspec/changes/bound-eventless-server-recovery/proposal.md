## Why

`fix(proxy): bound server recovery after eventless bridge failures` changes the
public recovery contract for anchored HTTP bridge continuations. Instead of
looping indefinitely while the server owns recovery, the bridge now stops after
`_HTTP_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS` consecutive eligible eventless
failures and emits one terminal `response.failed`.

That is observable behavior for operators and clients, and it was not captured
in an active OpenSpec change folder on this branch. The same terminal path also
needs to preserve a parseable `response.id`, because public `/v1/responses`
normalization synthesizes `response.created` from that terminal envelope.

## What Changes

- Document that server-indefinite bridge recovery is actually bounded by the
  configured attempt cap for eligible eventless anchored continuations.
- Require the terminal exhaustion event to include a stable `response.id` so
  public `/v1/responses` streams remain parseable when the first standard event
  is the exhaustion failure.

## Impact

- Affected capability: `responses-api-compat`.
- Affected code: `app/modules/proxy/api.py`, focused proxy error tests.
- No new settings or migrations.
