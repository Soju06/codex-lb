## 1. Spec
- [x] 1.1 Add a frontend-architecture delta that defines refreshing the browser OAuth link from the Accounts dialog.

## 2. Implementation
- [x] 2.1 Add a refresh action to the browser PKCE stage of the Accounts OAuth dialog.
- [x] 2.2 Reuse the existing browser OAuth start flow so refreshing mints a new authorization URL without leaving the dialog.

## 3. Validation
- [x] 3.1 Add or update frontend tests covering the refresh action.
- [x] 3.2 Run the targeted frontend OAuth dialog tests.
- [x] 3.3 Validate specs locally with `openspec validate --specs`.
