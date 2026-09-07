## Context

The native HTTP stream retry wrapper registers a dispatch owner for non-neutral input before processing a 401. Forced-refresh failure excludes that account, but the next selector still requires it. A separate policy keeps `reauth_required` accounts routable until known JWT expiry, so an upstream-rejected token with unknown or future expiry repeats this on new messages.

## Goals / Non-Goals

Goals: recover legal first-turn requests and make independently submitted messages avoid rejected, unrepairable access credentials. Preserve hard ownership, bounded attempts, settlement ordering, and concurrent credential repair.

Non-goals: blanket retries for `preferred_account_unavailable`, migration of opaque compaction or file state, replay after output, changes to refresh-token-only warnings, and deployment.

## Decisions

- Use the existing `account_auth_invalidated` persisted reason to distinguish failed access authentication from refresh-token-only failure. Apply it only after upstream 401 plus permanent forced-refresh failure or repeated post-refresh 401. Preserve that stronger reason when later refresh-only failures reaffirm the warning status.
- Add an optional access-token ciphertext predicate to the existing status CAS, alongside its refresh-token predicate. Do not add a schema field. A stale rejection must not mark a newer credential generation unavailable.
- Route exclusion reads the persisted reason in both selection and cluster-coherent bridge availability. Reimport/reauthentication resets status/reason through the existing repair path.
- Before cross-account replay, accept only known message/tool fields and reasoning bookkeeping through the existing projection, then require the complete resulting wire body to pass the canonical replay predicate. Never remove hosted-tool results, compaction, unresolved tool state, file references, previous-response, turn-state, single-account, or legacy hard-affinity constraints. Existing previous-response-specific error mapping is unchanged.
- Do not install a projected body when forced refresh succeeds: same-account retry retains the original input. Install a replacement and clear only its transient dispatch owner together when cross-account recovery is needed and authorized.
- Defer keyed account-health writes through the existing settlement queue. Exclude the rejected account locally immediately; commit the permanent routing evidence after settlement. Preserve the original authentication error when no replacement is legal.

## Risks / Trade-offs

- Unknown or opaque retained history cannot safely move. Return the original error without trying another account for that body; new unanchored requests can still use healthy accounts.
- Existing warning rows alone do not prove access rejection. Do not backfill them; the next observed rejection records the stronger reason.
- PR #2117 changes related reason-based routing. Keep this PR independent and cross-reference it for merge conflict coordination.

## Example

Account A rejects a full, self-contained text/tool transcript with `token_expired`; forced refresh returns `invalid_grant`. The request records A as authentication-invalidated, replays a validated account-neutral body on B, and completes. A subsequent independent message selects B without contacting A. Reimporting repaired credentials makes A eligible again.
