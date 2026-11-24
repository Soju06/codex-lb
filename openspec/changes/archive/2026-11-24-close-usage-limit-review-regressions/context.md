The review followed the merge of the dependency refresh into PR #1528. The corrections apply to the reserve policy and use the existing runtime sentinel table for migration recovery.

The cap check and later soft sticky draw can observe opposite sides of a cooldown deadline. For example, a capped prompt-cache owner can leave cooldown during the draw. This request uses a sibling with room and retains the existing mapping for future requests.

Lease release and affinity restoration can wait on different resources. Cancellation deferral keeps ownership of both operations with the rejected-probe cleanup while the conditional restore protects a newer owner. WebSocket finalization likewise needs ownership before cancellation can interrupt the sender.

An interrupted relative migration such as `+1` uses the original ledger to determine its target. A retry can finish at that revision after the ledger has been rewound. A separate explicit upgrade, such as `head`, can still advance the database.
