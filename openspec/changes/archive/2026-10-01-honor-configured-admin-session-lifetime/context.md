# Configured dashboard session lifetime

The persisted dashboardSessionTtlSeconds setting applies to newly issued
dashboard password sessions regardless of account role. The previous admin-only
resolver imposed a 12-hour remote ceiling even for explicitly configured values
within the existing 30-day boundary.

For example, a configured value of 1224000 seconds now produces a remote admin
cookie with Max-Age=1224000 and the same absolute server expiry. A default
one-year value still falls back to 12 hours remotely, and the explicit local
loopback policy still permits long local sessions.

Existing cookies are not extended when configuration changes. Session reuse does
not roll the expiry forward. OIDC provider caps, TOTP step-up windows, account
generation revocation and CSRF boundaries retain their existing checks. Logout
continues clearing the client cookie; the session payload is stateless.

This change adds no setting, schema, frontend or deployment. Verification uses
synthetic identities and isolated databases; no production cookie or password
is needed.
