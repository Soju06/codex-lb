# Design

Join the original reset-pool revision and current main revision with an explicit no-op Alembic merge. Preserve both histories without changing either parent's schema operations. A merge-only downgrade restores both parent stamps and keeps their tables and data. Test both starting branches with existing settings and an exact redemption binding, then check schema drift after re-upgrade.
