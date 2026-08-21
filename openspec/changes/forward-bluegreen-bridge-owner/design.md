## Decision

Fix the ring endpoint resolution layer. The bridge already has safe owner
forwarding with loop prevention and relay failure handling, and the durable
session claim path already fences live `ACTIVE` and `DRAINING` owners. The
missing piece is that local blue-green ring rows may have no endpoint metadata,
so owner forwarding is never attempted.

When `bridge_ring_members.metadata_json` has no usable `endpoint_base_url`,
`resolve_endpoint(instance_id)` derives `http://<instance-id>:2455` if the
instance id is a safe hostname token. This matches the helper's single-host
blue-green topology, where backends share a Docker network and are addressable
by container/ring identity. Explicit metadata still wins.

## Rejected Options

Ownership transfer was rejected for this incident because the retiring instance
may still be streaming the turn. Claiming its live lease would risk a
split-brain double stream.

Deploy-helper drain-only release was rejected as the primary fix because drain
already marks bridge owners `DRAINING`, and releasing active sessions at drain
start is unsafe. Idle-session release may be useful later, but it does not
serve a request whose live owner is still processing.

Returning only a weaker retryable client signal was rejected because the proxy
can route to the owner in the deployment overlap. A local client retry still has
the same router target and can burn its reconnect budget.
