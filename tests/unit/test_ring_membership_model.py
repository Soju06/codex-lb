from sqlalchemy import inspect as sa_inspect

from app.db.models import BridgeRingMember


def test_bridge_ring_member_tablename() -> None:
    assert BridgeRingMember.__tablename__ == "bridge_ring_members"


def test_bridge_ring_member_columns() -> None:
    mapper = sa_inspect(BridgeRingMember)
    cols = {col.key for col in mapper.attrs}
    assert {"id", "instance_id", "registered_at", "last_heartbeat_at", "metadata_json"}.issubset(cols)


def test_bridge_ring_member_instance_id_unique() -> None:
    mapper = sa_inspect(BridgeRingMember)
    instance_id_col = mapper.attrs["instance_id"].columns[0]
    assert instance_id_col.unique is True
