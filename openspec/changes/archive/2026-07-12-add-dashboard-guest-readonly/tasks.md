## 1. Authorization Model

- [x] 1.1 Add dashboard role/permission primitives for `admin` and `guest`.
- [x] 1.2 Extend dashboard sessions so new guest sessions carry role metadata while old admin sessions remain valid.
- [x] 1.3 Add a central write-permission dependency for mutating dashboard routes.

## 2. Guest Access Configuration

- [x] 2.1 Persist `guest_access_enabled` and optional `guest_password_hash` on dashboard settings.
- [x] 2.2 Expose guest access state in settings and auth session responses.
- [x] 2.3 Add guest password set/remove and guest login endpoints.
- [x] 2.4 Support guest access with no guest password when enabled.

## 3. Dashboard UI

- [x] 3.1 Parse session role/permissions and settings guest fields.
- [x] 3.2 Add guest login affordance for password-protected guest mode.
- [x] 3.3 Disable or hide write controls when the current principal is read-only.
- [x] 3.4 Add settings controls for enabling guest access and managing the optional guest password.

## 4. Verification

- [x] 4.1 Add backend coverage for passwordless guest read access and write denial.
- [x] 4.2 Add backend coverage for password-protected guest login and write denial.
- [x] 4.3 Add frontend schema/component coverage for guest session state and read-only controls.
- [x] 4.4 Run targeted backend and frontend tests plus lint/spec validation where available.
