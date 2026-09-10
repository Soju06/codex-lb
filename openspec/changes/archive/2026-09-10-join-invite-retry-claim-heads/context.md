# Invite and retry-claim history

Main561311ded1d3191cf1ef271d4cd8ea97f8fd17a4 adds040000 after audit030000 while PR1954 already publishes200000 joining170000 with030000. Preserve both histories and append a new join.

An account/retry-parent database gains an empty invite table while retaining its role grants, user/session generations, guest generation, audit rows and receipt/spool values. An invite-parent database retains its token hash, expiry, flags, issuer snapshot and user linkage while gaining nullable receipt columns. After populating both schemas, join-only downgrade changes stamps and nothing else.

Old source/review/hosted receipts remain immutable. Current-target compatibility requires this new join but introduces no invitation or authorization policy change.
