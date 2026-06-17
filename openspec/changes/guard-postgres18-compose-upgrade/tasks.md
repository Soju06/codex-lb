## 1. Compose upgrade guard

- [x] 1.1 Mount the Postgres named volume at the Postgres 18 parent data directory.
- [x] 1.2 Add a one-shot `postgres-upgrade` profile using the digest-pinned `pgautoupgrade/pgautoupgrade:18-alpine@sha256:9fdd8f8da95a5c4ca041290bfe966cd22ccd66f9f3bece17a0d37bd3b7e0a260` image.
- [x] 1.3 Refuse normal Postgres 18 startup when the named volume still has the pre-18 root `PG_VERSION` marker.
- [x] 1.4 Refuse nested legacy `data/PG_VERSION` layouts that still report a pre-18 major version.
- [x] 1.5 Preserve runtime Postgres command arguments while running the startup guard.

## 2. Operator documentation

- [x] 2.1 Document the stop, backup, upgrade, start, and database-check sequence.
- [x] 2.2 Document the fail-fast guard and required recovery action.

## 3. Validation

- [x] 3.1 Validate the OpenSpec change strictly.
- [x] 3.2 Validate the Compose service shape.
