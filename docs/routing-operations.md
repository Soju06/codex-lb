# Inspect and operate account routing

Contracts: [sticky-session operations](../openspec/specs/sticky-session-operations/spec.md),
[dashboard](../openspec/specs/frontend-architecture/spec.md),
[Responses compatibility](../openspec/specs/responses-api-compat/spec.md).

## Find the account behind a request

On **Dashboard**, open **View Details** on the affected request. **Account ID**
identifies the selected account. **Historical routing** shows the recorded
sticky source, kind and hash. It describes that request, rather than promising
that the session still uses the same account. The hash is not the raw sticky
key; find the raw mapping in **Settings → Show advanced settings → Sticky sessions**.

On **Accounts**, select the account. The routing banner shows the configured
strategy and that account's policy/status. **Local routing observation** shows
recent errors, active streams/creates, last selection and backoff deadlines.
It refreshes every 15 seconds and describes the current replica. Model support,
API-key assignment, additional quota, hard ownership and other replicas can
constrain selection separately. An expired displayed deadline is historical.

`fill_first` intentionally concentrates traffic. `burn_first` gives only the
accounts you choose priority. `preserve` holds capacity back. Change these
with the existing per-account **Routing policy** control; the update does not
mark every account burn-first or reset the global strategy.

## Test one account

Select an account on **Accounts**, then **Test account**. Enter a model slug
such as `gpt-6-astra`, or leave the model blank for the default. Press **Run test**.
The test sends a tiny real prompt using only that account and its configured
proxy, consumes a small amount of quota, and waits up to 30 seconds for
`response.completed`. It never falls back to a different account.

The result displays the model, upstream HTTP status, elapsed time and completion
outcome. HTTP 200 with a failed/incomplete stream is a failed test. Authentication,
quota/model errors and transport failures can therefore be distinguished from
successful generation. A successful small test does not guarantee that every
large request or every model is accepted. No credentials or generated text are
returned in the result.

The API preserves the original limiter-wakeup mode:
`POST /api/accounts/{id}/probe` with `{ "mode": "wakeup" }`, or no mode.
Use `{ "mode": "completion", "model": "gpt-6-astra" }` for the complete test.
Both endpoints require dashboard authentication and account-write permission.

## Choose another account or pause a bad one

In **Settings → Show advanced settings → Sticky sessions**, find a `prompt_cache` row and click
**Switch account**. Select another account. The switch changes the soft cache
preference used for future requests. If another request changed the mapping
since it was displayed, the API refuses the stale update: reload and try again.
Normal scope, model, quota and health rules still apply to the next request.

The button is intentionally unavailable for hard `codex_session` ownership and
other unsupported mappings. Files, previous-response IDs, turn state and live
bridges cannot be migrated by changing a soft preference. Requests already
running keep their owner. A hard continuation may need its original account
repaired or the client to resend a verified self-contained history.

Use **Pause** on Accounts to exclude an account from new selection without
deleting its credentials or chat history. **Resume** makes a paused account
available for normal eligibility checks again. If it needs reauthentication,
use **Re-authenticate**; repeated refresh attempts cannot repair a revoked
single-use token. Pausing an owner may prevent its hard continuations from
running until it is resumed or repaired.
