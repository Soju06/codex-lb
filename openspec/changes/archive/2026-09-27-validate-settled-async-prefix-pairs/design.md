## Context

The existing async continuity contract requires malformed asynchronous replay history to fail closed. Main's synchronous prefix proof validates ordering rather than full item shape; that unchanged policy is not expanded here. Sequential settled malformed pairs reproduce on main, but an intervening user turn before the async output is newly admissible in #2099 and exposes the missing validation.

## Decision

Collect async prefix calls and matching outputs independently of the outstanding-call map, then validate that complete sequence using the existing self-contained validator before returning prefix state. Removing a settled identity must not remove its validation evidence. Both durable matchers share this proof. Keep the existing outstanding map for suffix outputs and avoid a new persistence mechanism.

## Example

A prefix containing an async call, intervening user turn and matching output without an `output` field must not authorize owner-bound unanchored reattachment. The same sequence with a valid typed result remains admissible. Synchronous historical ordering, delayed suffix results and ownership checks retain their existing semantics.
