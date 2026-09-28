# Quarantine evidence lifetime

The Responses bridge already quarantines dead anchors and wedged sessions. Entry-local counters recycle after TTL pruning; delayed completion cleanup can then mistake new evidence for the evidence it observed. This capability owns only evidence lifetime. Request classification and owner-forward compatibility remain governed by responses-api-compat.

Capture before awaits, including alias persistence and durable loading. For example, completion observes poison generation 1, then settlement records the first eventless strike at generation 2. Cleanup removes poison 1 but retains strike 2 so the next timeout reaches the existing threshold. A durable-only poison arm discovered after an absent observation stays fenced through that completion. After successful settlement and fresh-anchor registration, the next request's first-touch load revokes the poison arm when it accepts a zero-failure row without an abandonment tombstone and the stale-load and newer-local-failure guards pass. Reuse selection then clears the stale session flag once no independent quarantine evidence remains. Failed settlement, an unreadable row, an abandonment tombstone, or newer poison evidence can keep the fence active longer. Independent weaker quarantine and later first-strike evidence remain protected. The completion never recaptures its fence after an await.

At saturation, one service-level deadline conservatively covers unknown keys. This can temporarily suppress unrelated anchorless deltas, but avoids either dropping poison proof or unbounded per-key allocation. Full-history recovery and TTL remain existing recovery paths. No new setting or durable schema is introduced.

The quarantine module remains cohesive despite exceeding 300 lines: generation allocation, admission, provenance and cleanup share one entry invariant. Extraction by size would spread that invariant across modules without a narrower ownership boundary.

This branch records implemented candidate behavior. Owner acceptance of bounded overflow and durable-only poison cleanup timing remains pending in the active change; local tests do not approve either tradeoff.
