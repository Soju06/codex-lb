# Change: add upstream proxy dashboard controls

## Why
The backend upstream proxy routing API is merged, but operators cannot safely configure endpoint pools, default routing, or per-account bindings from the dashboard. This leaves the account-bound proxy safety feature effectively API-only.

## What changes
- Add frontend schemas and API calls for upstream proxy admin state and mutations.
- Add settings UI controls for routing enablement, default pool selection, endpoint creation, pool creation, and pool membership.
- Add account-level proxy pool binding controls so operators can route all ChatGPT traffic for an account through its configured pool.
- Add dashboard tests and mock handlers for the new operator flows.

## Non-goals
- Editing/deleting existing proxy endpoints or pools.
- Showing proxy credentials after creation.
- Runtime TLS fingerprint/profile controls.
