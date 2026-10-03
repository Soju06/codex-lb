"""Model→plan filter phải "fail-open" khi registry chưa biết model.

Bối cảnh: registry được làm mới theo từng plan; nếu lần refresh đó thiếu một model
(ví dụ `gpt-6-astra` không xuất hiện trong kết quả của plan nào), trước đây
`plan_types_for_model()` trả frozenset rỗng → lọc sạch mọi tài khoản → proxy trả
`no_plan_support_for_model` dù tài khoản hoàn toàn dùng được. Nay trường hợp
"không biết" (None hoặc rỗng) không được lọc cứng.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.db.models import AccountStatus
from app.modules.proxy import load_balancer as lb


def _account(plan: str) -> AsyncMock:
    account = AsyncMock()
    account.plan_type = plan
    account.status = AccountStatus.ACTIVE
    return account


def test_filter_accounts_for_model_keeps_all_when_registry_unknown(monkeypatch) -> None:
    monkeypatch.setattr(lb.get_model_registry(), "plan_types_for_model", lambda slug: None)
    accounts = [_account("plus"), _account("free")]
    assert lb._filter_accounts_for_model(accounts, "gpt-6-astra") == accounts


def test_filter_accounts_for_model_keeps_all_when_plans_empty(monkeypatch) -> None:
    monkeypatch.setattr(lb.get_model_registry(), "plan_types_for_model", lambda slug: frozenset())
    accounts = [_account("plus")]
    assert lb._filter_accounts_for_model(accounts, "gpt-6-astra") == accounts


def test_filter_accounts_for_model_filters_when_registry_knows(monkeypatch) -> None:
    monkeypatch.setattr(lb.get_model_registry(), "plan_types_for_model", lambda slug: frozenset({"plus"}))
    plus, free = _account("plus"), _account("free")
    assert lb._filter_accounts_for_model([plus, free], "gpt-6-astra") == [plus]
