# Decision notes

## Docker dashboard password recovery for v1.24.0

- **Why:** The v1.24.0 image has no `admin reset-password` CLI, so the current troubleshooting link alone cannot recover a forgotten password on that image.
- **Ownership:** Keep the historical workaround as a version-named downloadable docs asset, linked from troubleshooting. Newer builds with the host recovery CLI use `docs/sso.md`; this script must reject other versions and non-SQLite databases.
- **Security:** Take and verify an online SQLite backup before changing credentials; keep the backup private. Prompt on a TTY so the password never appears in shell history or process arguments. Use v1.24.0's own credential writer to preserve TOTP and clear the bootstrap token. Its stateless dashboard sessions remain valid until expiry.
- **Naming:** The `1.24` suffix marks the release-specific procedure; do not present it as a generic reset tool.
