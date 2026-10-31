from __future__ import annotations

from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

from app.core.usage.credit_pricing import CREDIT_SCALE, CreditPricingUnavailable, credit_units_for_usage

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "model,input_tokens,cached_tokens,output_tokens,tier,expected",
    [
        ("gpt-6-astra", 1_000_000, 0, 1_000_000, None, 150_000_000_000),
        ("gpt-6.1-sol", 1_000_000, 1_000_000, 0, None, 250_000_000),
        ("gpt-6.1-terra", 1_000_000, 0, 0, None, 2_500_000_000),
        ("gpt-6-sol", 1_000_000, 1_000_000, 0, None, 500_000_000),
        ("gpt-6-luna", 1, 1, 0, None, 25),
        ("gpt-5.6-sol", 1_000_000, 0, 0, "fast", 20_000_000_000),
        ("gpt-5.6-sol", 1_000_000, 0, 0, "priority", 20_000_000_000),
        ("gpt-6.1-sol", 1_000_000, 0, 0, "ultrafast", 30_000_000_000),
        ("gpt-5.6-terra", 0, 0, 1_000_000, "default", 30_000_000_000),
        ("gpt-5.6-luna", 1_000_000, 1_000_000, 0, "auto", 50_000_000),
        ("gpt-6.1-sol", 1, 20, -5, None, 250),
        ("gpt-6.1-sol", 1, -20, 0, None, 5_000),
        ("gpt-6.1-sol", -10, 0, -5, None, 0),
    ],
)
def test_published_credit_rates(
    model: str, input_tokens: int, cached_tokens: int, output_tokens: int, tier: str | None, expected: int
) -> None:
    assert (
        credit_units_for_usage(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
            service_tier=tier,
        )
        == expected
    )


@pytest.mark.parametrize(
    "model,tier",
    [
        (None, None),
        ("unpriced", None),
        ("gpt-6.1-sol", "unpriced"),
        ("gpt-5.6-sol", "ultrafast"),
        ("gpt-5.6-terra", "flex"),
    ],
)
def test_unpriced_usage_is_not_free(model: str | None, tier: str | None) -> None:
    with pytest.raises(CreditPricingUnavailable):
        credit_units_for_usage(model, input_tokens=0, output_tokens=0, service_tier=tier)


def test_credit_storage_preserves_sub_microcredit_requests() -> None:
    # Four cached GPT-6 Luna tokens cost one microcredit, without per-call rounding.
    charges = [credit_units_for_usage("gpt-6-luna", input_tokens=1, cached_input_tokens=1, output_tokens=0)] * 4
    assert sum(charges) == 100
    assert CREDIT_SCALE == 100_000_000


def test_credit_charge_is_bounded_by_integer_storage() -> None:
    assert credit_units_for_usage("gpt-6-astra", input_tokens=2**62, output_tokens=2**62) == 2**63 - 1


@pytest.fixture
def _credit_migration_context(monkeypatch: pytest.MonkeyPatch):
    migration = import_module("app.db.alembic.versions.20261030_000000_scale_api_key_credit_counters")
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE api_key_limits (limit_type TEXT, max_value BIGINT, current_value BIGINT)")
            )
            connection.execute(
                text(
                    "CREATE TABLE api_key_usage_reservation_items "
                    "(limit_type TEXT, reserved_delta BIGINT, actual_delta BIGINT)"
                )
            )
            connection.execute(text("CREATE TABLE api_key_usage_reservations (id TEXT)"))
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            yield migration, connection
    finally:
        engine.dispose()


def test_credit_migration_preserves_counters_and_reservations(_credit_migration_context) -> None:
    migration, connection = _credit_migration_context
    connection.execute(text("INSERT INTO api_key_limits VALUES ('credits', 60, 2), ('total_tokens', 100, 7)"))
    connection.execute(text("INSERT INTO api_key_usage_reservation_items VALUES ('credits', 1, NULL)"))
    connection.execute(text("INSERT INTO api_key_usage_reservations VALUES ('existing')"))
    migration.upgrade()
    migration.upgrade()  # Rewound ledger: current units must not be scaled twice.
    assert connection.execute(text("SELECT max_value, current_value FROM api_key_limits")).all() == [
        (6_000_000_000, 200_000_000),
        (100, 7),
    ]
    assert connection.execute(
        text("SELECT reserved_delta, actual_delta FROM api_key_usage_reservation_items")
    ).one() == (100_000_000, None)
    assert connection.execute(text("SELECT credit_service_tier FROM api_key_usage_reservations")).one() == (None,)
    migration.downgrade()
    migration.downgrade()
    assert connection.execute(text("SELECT max_value, current_value FROM api_key_limits")).all() == [(60, 2), (100, 7)]
    assert connection.execute(
        text("SELECT reserved_delta, actual_delta FROM api_key_usage_reservation_items")
    ).one() == (1, None)


def test_credit_migration_checks_all_tables_before_conversion(_credit_migration_context) -> None:
    migration, connection = _credit_migration_context
    connection.execute(text("INSERT INTO api_key_limits VALUES ('credits', 1, 0)"))
    connection.execute(text("INSERT INTO api_key_usage_reservation_items VALUES ('credits', 92233720369, NULL)"))
    with pytest.raises(RuntimeError, match="storage range"):
        migration.upgrade()
    assert connection.execute(text("SELECT max_value FROM api_key_limits")).scalar_one() == 1
