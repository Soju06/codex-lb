# Beta.6 integration context

The approved source is v1.25.0-beta.6, peeled commit `e4c0164daae7e3ee3983d4455dee174441f76355`. The starting fork commit is `7ecb38deb2157aab7f7a13cb22bf8b23be16fb2c`.

The API-key usage-group migration has already been deployed. For example, an existing key with usage group `team-a` must still belong to `team-a` after the beta.6 report tables are added. A new merge revision joins both lineages without changing the deployed revision's parent.

The fork permits bursts above 64 native WebSocket events while enforcing shared byte budgets. Upstream response interpretation must coexist with that behavior. Failure-phase diagnostics and queued terminal delivery remain necessary for correct retry decisions.

Existing four-fix backport verification is historical beta.5 evidence, not beta.6 verification. Record fresh verification for this merge separately. No commit, push, deployment, or unrelated installer edit is included.

The previously approved reauthentication quarantine also remains: unlike stock
upstream, this fork does not route `reauth_required` accounts while their old
access tokens remain unexpired. Conflict resolution removes the incompatible
upstream routability requirement and retains the fork's hard gate. No new
exception to quarantine is introduced.

Legacy migration tests build schemas from current models and then deliberately
rewind revision stamps. Their fixture now removes the fork-only group column
before simulating a pre-group revision, so the immutable deployed group DDL is
not falsely re-applied over a column the fixture created. Real upgrades from the
deployed stamped group head are tested separately with retained data.
