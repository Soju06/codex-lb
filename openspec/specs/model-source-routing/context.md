# Model Source Routing — Context

## Purpose

Capability-based routing and accounting for OpenAI-compatible model sources,
including field-preserving embeddings forwarding.

This capability keeps source selection separate from subscription-account
routing: embeddings traffic is served only by sources that declare the
embeddings capability, while Responses/chat/audio continue to use their own
capability gates. Field presence (including explicit nulls) is preserved on
embeddings forwards so compatible sources see the same payload shape the
client sent.


## Public model aliases

Operators may enter `public-model=provider-model` in the model-source form. The public ID governs selection, permissions and accounting; the selected source resolves its upstream target only at forwarding. Structured response model fields return to the public ID without rewriting generated text or tool arguments. A missing mapping preserves the existing identity behavior.

# Source reference ownership

Equivalent sources expose the same public model through separate credentials. Fresh source-neutral Responses requests may select an available authorized credential. References to responses, tool calls, items, prompts, containers and vector stores must remain on the owning source and credential revision. Publishing ownership before returning output prevents a later replica from guessing a different credential.

For example, a client receives `resp-example` from source A. A continuation with that ID stays on A even if B is less busy. Disabling A or changing its credential causes an ownership error before reservation/dispatch. Historical evidence survives live-row expiry and source deletion; empty historical revision evidence cannot prove safe reuse. Uploaded subscription files cannot enter direct-source routes.

Selection tracks in-flight requests and bounded cooldowns locally; reference ownership is shared through the database. Each failed attempt settles/releases before source health changes or another attempt begins. No AsyncSession is shared by concurrent work. Retries stop after five candidates, before externally visible output, and never move source-bound state.

No subscription-overflow path is introduced. Tool support retains upstream's explicit metadata policy, including `experimental_supported_tools` for namespace declarations. Alias IDs govern client permissions and accounting; targets resolve only after source selection.

Two additive migrations create active ownership and durable history tables plus nullable source revision on request logs. Existing log rows keep null revisions. PostgreSQL/cloud migration gates remain required before merge. History is deliberately retained to avoid reassigning stale references after active-row expiry; its unbounded storage lifetime needs maintainer product review.
