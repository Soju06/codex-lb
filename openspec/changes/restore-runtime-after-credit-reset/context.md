# Context

PR #2640 supplies guarded confirmed-credit recovery of persisted account state.
A regression using the same LoadBalancer instance reproduced a remaining failure:
the reset endpoint returned success and the database became active, but the router
retained its old quota cooldown and returned "Try again in 300s".

The bounded runtime repair reuses quota-attributed error accounting from #2702,
without its badge API or natural-rollover wake behavior. It retires only older
quota holds after persisted recovery and a fresh available usage snapshot.
Unrelated transport error history, manual pauses, hard blocks and newer failures
remain protected. Usage snapshot timestamps now identify fetch start, not finish.

Deployment must coordinate usage writers: a mixed-version replica still writing
completion-time timestamps does not provide the fetch-start provenance required
by this evidence gate. The intended current deployment is a single live backend
with the updated writer and router installed together. Do not claim rolling
mixed-version recovery safety. No production credit is spent in verification.
