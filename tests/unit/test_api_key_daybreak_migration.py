from importlib import import_module

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_daybreak_migration_preserves_keys_and_defaults_off(monkeypatch):
    migration = import_module("app.db.alembic.versions.20261105_000000_add_api_key_daybreak_routing")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE api_keys (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO api_keys (id) VALUES ('existing')")
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert connection.exec_driver_sql("SELECT id, automatic_daybreak_routing FROM api_keys").all() == [
            ("existing", 0)
        ]
        connection.exec_driver_sql("INSERT INTO api_keys (id) VALUES ('new')")
        assert (
            connection.exec_driver_sql("SELECT automatic_daybreak_routing FROM api_keys WHERE id='new'").scalar() == 0
        )
        connection.exec_driver_sql("UPDATE api_keys SET automatic_daybreak_routing=1 WHERE id='existing'")
        assert (
            connection.exec_driver_sql("SELECT automatic_daybreak_routing FROM api_keys WHERE id='existing'").scalar()
            == 1
        )
        migration.upgrade()
        assert (
            connection.exec_driver_sql("SELECT automatic_daybreak_routing FROM api_keys WHERE id='existing'").scalar()
            == 1
        )
        migration.downgrade()
        migration.downgrade()
        assert connection.exec_driver_sql("SELECT id FROM api_keys ORDER BY id").all() == [("existing",), ("new",)]
        migration.upgrade()
        assert connection.exec_driver_sql("SELECT SUM(automatic_daybreak_routing) FROM api_keys").scalar() == 0
    engine.dispose()
