# SQLite completion-grace context

This is the DB portion of PR2030 and issue #2029. SSL caching has already shipped on the pinned main snapshot; the PR retains that private cache and its consumers without another TLS contract.

A bounded observation can expire while a worker has finished its native call but the loop has not propagated the result through the awaiting task. In controlled real file-SQLite probes, native rollback/close completed in less than1 ms while the loop was blocked120 ms across a50 ms initial bound. Grace observed success; disabling grace restored fencing/reclamation. This confirms a reachable ordering failure, not the cause or duration of any historical production stall.

A representative safe outcome is: worker finishes rollback, the loop resumes late, grace observes success, normal close runs, and a second writer succeeds. A failed or cancelled task does not prove the same outcome, particularly after SQLAlchemy clears the transaction reference. The captured connections and existing cleanup-task owner therefore remain necessary.

The initial wait and grace are separate bounded opportunities. An invalidation error is a diagnostic of failed cleanup, not proof of an everlasting lock. See the [delta](specs/database-backends/spec.md) and [design](design.md); the sibling `bound-sqlite-wedged-teardown` delta uses the same contract. No operator setting or migration is added.
