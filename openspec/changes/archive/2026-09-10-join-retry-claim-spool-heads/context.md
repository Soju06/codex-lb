# Join the receipt and spool-retention histories

The exact composition of PR1954 at cbdf5463 with main6d11e560 fails the public upgrade CLI with two heads. The heads are the prior receipt/request-log merge and the later spool-retention revision. This is independent of Git mergeability.

A new merge revision preserves installations stamped at either existing head. Reparenting an applied revision would change their migration histories, so it is excluded. The new revision performs no schema operation; Alembic applies the missing parent before joining the version stamps.

For example, a database on the receipt merge with a live receipt first receives the nullable spool-retention setting, then the merge stamp. Its receipt fields remain intact. A database on the spool branch receives the guarded receipt migration with nullable receipt fields. Downgrading only the merge changes stamps, not either schema. The existing receipt migration remains responsible for its active-receipt downgrade guard.

This repair does not select receipt lifetime, reclamation, success settlement or mixed-version activation policy. The separate receipt lifecycle change remains open. Verification covers the documented CLI, populated parents, merge-only downgrade/re-upgrade, existing receipt/spool round trips and drift checks.
