# Context

## What the reserve actually looks like

Observed on 2026-09-17, on a live instance with two Plus accounts. One of them
carried this entry in `additional_rate_limits`; the other carried none, so
eligibility is per-account and not a plan property:

```
limit_name       gpt-reserve
metered_feature  base_model_inference
primary_window   used_percent 0.0, window 10080 minutes (weekly)
secondary_window null
```

Two details shaped the registry entry. The bucket is weekly, not a 5h window,
so it does not track the short chat window at all. And upstream sends
`gpt-reserve` as the `limit_name` while the metered feature is
`base_model_inference` — the entry lists both, because either one can be the
field that arrives.

## Why not the automatic fallback

The first implementation watched for quota exhaustion and flipped the account
into a reserve mode that rewrote the model on the wire. Three things were wrong
with it beyond the design objection on #2413:

The mode was sticky. It was set inside `handle_quota_exceeded` and cleared only
by the *next* quota error. The recovery path that returns an account to `ACTIVE`
after a window reset did not know about it, so an account that recovered kept
routing every turn to the reserve while its ordinary quota sat unused.

Activation demoted the account it was trying to use. To keep the account
selectable it set `used_percent = 100.0`, which is also the input to the drain
threshold, so the account dropped to the bottom of the ranking at the moment it
became the only one that could serve the request.

The model name was put back by replacing the literal `"gpt-reserve"` across the
whole downstream payload, which would also rewrite the string inside text the
model itself produced.

None of this had ever run. Days of production logs on the prototype contained
zero `quota_exceeded` events, so the path was never entered even once. That is
the real argument for the explicit model: an operator-selected model is
exercised the first time someone picks it, while a fallback only runs in the
situation that is hardest to reach on purpose.

## Why `disabled` needed two edits

`load_balancer.py` kept its own copy of the valid policy set, assembled from the
account-level policies plus `inherit`, while `additional_quota_keys.py` held the
canonical set used by the registry loader. Adding `disabled` to only the
load-balancer copy would have left the loader normalizing it to `inherit` —
shipping the reserve *on* by default, the opposite of the intent, with no error
anywhere. The duplicate is now gone and the load balancer imports the canonical
set.

This is the same failure mode as the `rate_limit_upsell` key in the prototype:
a value that silently normalizes instead of failing. The tests for that code
built `UsagePayload(...)` in Python and so could never have caught a wrong wire
key, which is why the test here parses captured JSON instead.

## Why an invalid override is ignored, not normalized to `inherit`

An unrecognized routing policy in the *registry* normalizes to `inherit`: the
registry is trusted config and `inherit` is the safe reading of a value the
loader does not understand. A dashboard *override* is different. The settings
API types it as a bare `dict[str, str]` and does not reject an unknown value, so
the parser drops anything outside the canonical set and the quota falls back to
its registry default. Normalizing a bad override to `inherit` instead would let
a typo turn a `disabled`-by-default quota routable, silently defeating the off
switch; dropping it keeps the reserve off until an operator sets a value the code
recognizes. The spec was tightened to require this split — the earlier wording
normalized both the registry and the override path to `inherit`.

## Why `disabled` refuses before filtering

The additional-limit filter's result feeds a rescue: when it returns no
accounts and every candidate needs re-authentication, the candidates are used
anyway. That is correct for a quota that is merely exhausted and wrong for one
an operator switched off, so the gate returns before the filter runs rather
than inside it.

## The error contract

A refused request gets the house selection-failure mapping: HTTP 503 carrying
`additional_quota_routing_disabled` and the message verbatim. This matches
`no_plan_support_for_model`, which is likewise permanent until configuration
changes and likewise returns 503. The message names the display label so the
operator has something to search for in Settings.
