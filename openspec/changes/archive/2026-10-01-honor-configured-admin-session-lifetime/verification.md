# Verification

The product-path regression failed three cases on unchanged main: configured
1224000 seconds, configured 2592000 seconds and reuse beyond twelve hours.
The short configured value and above-boundary fallback cases already passed.

- 378 tests passed in the selected authentication, password, session-store,
  locality, OIDC, TOTP, CSRF, permission and dashboard-user suites.
- Five changed Python files have clean language-server diagnostics.
- Ruff check/format, application ty, wheel build and strict change validation
  passed. The owning admin-auth requirements and context are synced.
- Six real HTTP scenarios passed with synthetic credentials and isolated data.
  Configured values 3600, 1224000 and 2592000 produce those exact cookie ages.
  Values 2592001 and 31536000 retain the 43200-second remote fallback.
- Cookie reuse returns 200; logout returns 200 and clears the client cookie
  with Max-Age=0, preserving the existing stateless logout contract.
- With a controlled session clock, requests after twelve hours and just before
  the configured expiry return 200 without rolling or replacing the cookie.
  Just after expiry, the same cookie returns 401/authentication_required.
- Real app routes, settings persistence and session validation were exercised.
  Only unrelated background schedulers were quarantined. The server/socket were
  closed and the temporary database and separate test databases removed.

The first auxiliary HTTP run incorrectly expected a stateless decoded payload
to disappear after logout. Existing delete() intentionally clears only the
client cookie; the harness was corrected to assert the actual wire contract.
No production behavior or repository test was changed to satisfy that mistake.

Full local CI cannot complete in the existing environment without cargo-deny.
Global strict specs have the previously recorded upstream failures. These are
not claimed green. The existing dependency lock also contains urllib3 2.7.0
flagged by the current GitHub Trivy gate; no dependency upgrade is included.

Self-review confirms one existing role-independent resolver replaces the
duplicated admin policy, while maximum provider lifetimes, step-up windows,
revocation and locality protections retain their checks.
