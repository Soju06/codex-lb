# Repair scope

Desktop's native Reset dialog reaches LB through the relay. Its usage envelope carries an optional reset-credit summary. The relay, authenticated identity and usage adapter are included in this PR so the complete native action works from current main without an unmerged dependency. Removing only the relay would leave the normal Reset action reaching the original upstream account.

The existing immutable binding contract requires a permanent conflict response when the helper ledger disagrees with the permanent Desktop ledger. Inventory refresh snapshots token and upstream account identity before releasing its database session; upstream requests consume no database pool slot while waiting for HTTP. Eligibility is rechecked after refreshing and immediately before consuming.

Verification uses synthetic credits and disposable databases. Hosted PostgreSQL checks remain necessary; local SQLite proof does not establish PostgreSQL behavior or live Desktop acceptance.
