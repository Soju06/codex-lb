# Pool context management

## Why
The current context endpoints require a single-account API key, while codex-lb routes inference across a pool. The isolated prototype proved native notes can remain on one owner while another account performs inference. History is account-local and must preserve all participating accounts.

## What changes
- Persist context identity, notes ownership and history participation under the authenticated API key.
- Route notes to their owner independently of inference quota and preserve auth/scope boundaries.
- Support history recovery across participating accounts without decrypting or dropping native output. Establish the encrypted-output aggregation contract before implementation.
- Cover restart, concurrent requests, child agents, quota rotation and unavailable owners.

## Impact
Codex context routes, account dispatch observation, persistence, tests and operator documentation. Production remains unchanged during preparation. No dependency on open retry PRs is assumed.
