## Implementation and verification
- [x] Schedule successful refreshes before their freshness deadline.
- [x] Add regression coverage for normal, short-interval and failed refreshes.
- [x] Prove the deployed-source control fails the intended freshness invariant.
- [x] Validate the change and review the production candidate independently.
- [x] Qualify and safely adopt the correction without losing active requests.

See `verification.md` for the scope and limits of the completed checks. Passing
component checks and source review do not establish production adoption.
