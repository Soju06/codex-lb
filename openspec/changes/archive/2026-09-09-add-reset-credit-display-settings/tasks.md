## Tasks

- [x] Add dashboard settings fields, migration, backend schemas, service/repository/API plumbing.
- [x] Add reset-credit Settings UI and frontend schema/payload plumbing.
- [x] Wire display settings into Accounts list, Accounts action button expiry label, and top navigation badge.
- [x] Show nearest reset-credit expiry in the Accounts Usage panel.
- [x] Extend the reset-credit refresh scheduler to optionally auto-redeem soon-expiring credits using the existing redeem helper.
- [x] Add/run focused settings/API/UI regression tests.
- [x] Add/run mocked automatic redemption regression tests without contacting upstream or performing a live redeem.
- [x] Perform non-test verification: code-path inspection, static grep checks, and OpenSpec validation.
- [x] Tighten automatic redemption to a fixed five-minute window with duplicate-consume safeguards and matching UI copy.
- [x] Verify one live automatic redemption against the approved Funeasy account exactly once.
- [x] Include reset-credit settings in settings audit `changed_fields`.
- [x] Re-read account eligibility immediately before automatic redemption.
- [x] Constrain automatic redemption to the credit id and expiry that triggered the five-minute window.
