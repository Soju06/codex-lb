## 1. Dashboard surfaces

- [x] 1.1 Offer the resume control for `deactivated` on the accounts page actions panel, keeping re-authentication alongside it.
- [x] 1.2 Offer it on the dashboard account list row.
- [x] 1.3 Offer it on the dashboard account card.
- [x] 1.4 Leave `reauth_required` with re-authentication only.

## 2. Regression coverage

- [x] 2.1 Assert a deactivated account exposes both resume and re-authentication, and that resume invokes the callback with the account id.
- [x] 2.2 Assert a `reauth_required` account exposes no resume control.

## 3. Verification

- [x] 3.1 Run the accounts and dashboard component tests.
- [x] 3.2 Run the frontend typecheck and lint.
- [x] 3.3 Validate the scoped OpenSpec change with strict validation.
- [ ] 3.4 Capture dashboard before/after screenshots.
