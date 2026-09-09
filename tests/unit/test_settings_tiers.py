"""Tests for the configuration-tier guard (``scripts/check_settings_tiers.py``).

Live-tree assertions keep ``app/core/config/tiers.py`` in step with
``Settings`` and only assert on errors (stale entries are warnings, so a field
removal landing first keeps this suite green); the fixture-based tests pin the
checker's error/warning semantics so field removals (stale entries warn) and
env-read cleanups (stale allowlist entries warn) can land in either order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config.settings import Settings
from app.core.config.tiers import DASHBOARD_HOMES, MIGRATING, SETTING_TIERS, TIERS
from app.db.models import Base, DashboardSettings
from scripts import check_settings_tiers as checker

pytestmark = pytest.mark.unit

FIELDS = ("alpha", "beta", "gamma")
TIER_MAP = {"alpha": "T0", "beta": "T3", "gamma": "T1"}


def _dashboard_columns() -> list[str]:
    return [column.name for column in DashboardSettings.__table__.columns]


def _table_columns() -> dict[str, list[str]]:
    return {name: [column.name for column in table.columns] for name, table in Base.metadata.tables.items()}


def test_every_live_settings_field_has_a_tier() -> None:
    report = checker.check_tier_coverage(Settings.model_fields, SETTING_TIERS, TIERS)
    assert report.errors == []


def test_every_live_t3_field_has_a_dashboard_home_or_migrating_entry() -> None:
    report = checker.check_t3_dashboard_home(
        Settings.model_fields, SETTING_TIERS, MIGRATING, _dashboard_columns(), DASHBOARD_HOMES, _table_columns()
    )
    assert report.errors == []


def test_live_dashboard_homes_are_not_duplicated_in_migrating() -> None:
    # A live field is either migrating (env-only) or already homed; never both.
    # Entries for removed fields are left to the checker's stale-entry warning.
    live_homes = {name for name in DASHBOARD_HOMES if name in Settings.model_fields}
    assert live_homes & set(MIGRATING) == set()
    assert all(SETTING_TIERS.get(name) == "T3" for name in live_homes)


def test_live_tree_passes_all_checks() -> None:
    reports, summary = checker.run_all()
    assert [message for report in reports for message in report.errors] == []
    assert summary.startswith(f"settings fields: {len(Settings.model_fields)}/")


def test_missing_tier_is_an_error_and_stale_entry_only_warns() -> None:
    tiers = {"alpha": "T0", "gamma": "T1", "removed_field": "T3"}
    report = checker.check_tier_coverage(FIELDS, tiers, TIERS)
    assert report.errors == ["Settings.beta has no tier in app/core/config/tiers.py SETTING_TIERS"]
    assert len(report.warnings) == 1
    assert "removed_field" in report.warnings[0]


def test_unknown_tier_value_is_an_error() -> None:
    report = checker.check_tier_coverage(("alpha",), {"alpha": "T9"}, TIERS)
    assert len(report.errors) == 1
    assert "'T9'" in report.errors[0]


def test_t3_field_without_dashboard_column_or_migrating_entry_fails() -> None:
    report = checker.check_t3_dashboard_home(FIELDS, TIER_MAP, {}, ["unrelated_column"])
    assert len(report.errors) == 1
    assert report.errors[0].startswith("Settings.beta is T3 but has neither a dashboard_settings column")


def test_t3_field_with_explicit_dashboard_home_passes() -> None:
    homes = {"beta": "dashboard_settings.beta_decision"}
    report = checker.check_t3_dashboard_home(FIELDS, TIER_MAP, {}, ["beta_decision"], homes)
    assert report.errors == []
    assert report.warnings == []
    other_table = checker.check_t3_dashboard_home(
        FIELDS, TIER_MAP, {}, [], {"beta": "beta_consent.decision"}, {"beta_consent": ["decision"]}
    )
    assert other_table.errors == []
    assert other_table.warnings == []


def test_explicit_dashboard_home_must_name_an_existing_column() -> None:
    missing = checker.check_t3_dashboard_home(FIELDS, TIER_MAP, {}, ["unrelated"], {"beta": "dashboard_settings.nope"})
    assert missing.errors == [
        "DASHBOARD_HOMES maps 'beta' to 'dashboard_settings.nope', but no such database column exists"
    ]
    unknown_table = checker.check_t3_dashboard_home(FIELDS, TIER_MAP, {}, [], {"beta": "ghost_table.decision"})
    assert unknown_table.errors == [
        "DASHBOARD_HOMES maps 'beta' to 'ghost_table.decision', but no such database column exists"
    ]
    malformed = checker.check_t3_dashboard_home(FIELDS, TIER_MAP, {}, ["beta_decision"], {"beta": "beta_decision"})
    assert malformed.errors == ["DASHBOARD_HOMES['beta'] = 'beta_decision' is not a 'table.column' target"]


def test_redundant_dashboard_home_entries_warn() -> None:
    homes = {
        "beta": "dashboard_settings.beta",
        "alpha": "dashboard_settings.alpha_decision",
        "removed_field": "dashboard_settings.alpha_decision",
    }
    report = checker.check_t3_dashboard_home(FIELDS, TIER_MAP, {"beta": "backlog"}, ["beta", "alpha_decision"], homes)
    assert report.errors == []
    assert sorted(report.warnings) == sorted(
        [
            "DASHBOARD_HOMES lists 'alpha', which is 'T0', not T3; drop the entry",
            "DASHBOARD_HOMES lists 'beta', but dashboard_settings.beta exists; drop the entry",
            "DASHBOARD_HOMES lists 'removed_field', which is no longer a Settings field; drop the entry",
            "MIGRATING lists 'beta', but dashboard_settings.beta exists; drop the entry",
        ]
    )
    # Redundant entries whose old target is gone or malformed: still warnings, never errors
    # (field removed, field re-tiered, or a same-name column landed and the old column was dropped).
    stale_target_gone = checker.check_t3_dashboard_home(
        FIELDS,
        TIER_MAP,
        {},
        ["beta"],
        {
            "removed_field": "dashboard_settings.gone",
            "also_removed": "not-a-target",
            "alpha": "dashboard_settings.gone",
            "beta": "not-a-target",
        },
    )
    assert stale_target_gone.errors == []
    assert stale_target_gone.warnings == [
        "DASHBOARD_HOMES lists 'alpha', which is 'T0', not T3; drop the entry",
        "DASHBOARD_HOMES lists 'also_removed', which is no longer a Settings field; drop the entry",
        "DASHBOARD_HOMES lists 'beta', but dashboard_settings.beta exists; drop the entry",
        "DASHBOARD_HOMES lists 'removed_field', which is no longer a Settings field; drop the entry",
    ]
    homed_and_migrating = checker.check_t3_dashboard_home(
        FIELDS, TIER_MAP, {"beta": "backlog"}, ["beta_decision"], {"beta": "dashboard_settings.beta_decision"}
    )
    assert homed_and_migrating.errors == []
    assert homed_and_migrating.warnings == [
        "MIGRATING lists 'beta', but DASHBOARD_HOMES already maps it to 'dashboard_settings.beta_decision'; "
        "drop the entry"
    ]


def test_t3_field_with_same_name_dashboard_column_passes() -> None:
    report = checker.check_t3_dashboard_home(FIELDS, TIER_MAP, {}, ["beta"])
    assert report.errors == []
    assert report.warnings == []


def test_t3_field_with_migrating_entry_passes_and_redundant_entries_warn() -> None:
    migrating = {"beta": "backlog", "alpha": "backlog", "removed_field": "backlog"}
    report = checker.check_t3_dashboard_home(FIELDS, TIER_MAP, migrating, [])
    assert report.errors == []
    assert sorted(report.warnings) == sorted(
        [
            "MIGRATING lists 'alpha', which is 'T0', not T3; drop the entry",
            "MIGRATING lists 'removed_field', which is no longer a Settings field; drop the entry",
        ]
    )
    redundant = checker.check_t3_dashboard_home(FIELDS, TIER_MAP, {"beta": "backlog"}, ["beta"])
    assert redundant.errors == []
    assert redundant.warnings == ["MIGRATING lists 'beta', but dashboard_settings.beta exists; drop the entry"]


def _write(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_env_read_detection_flags_every_form_and_skips_settings_module(tmp_path: Path) -> None:
    _write(tmp_path, "app/core/config/settings.py", "import os\nX = os.environ.get('A')\n")
    _write(tmp_path, "app/a.py", "import os\n\nvalue = os.environ.get('A')\nother = os.getenv('B')\n")
    _write(tmp_path, "app/b.py", "from os import getenv as read_env\n\nvalue = read_env('A')\n")
    _write(tmp_path, "app/c.py", "from dotenv import dotenv_values\n\nvalues = dotenv_values('.env')\n")
    _write(tmp_path, "app/d.py", "import dotenv\n\nvalues = dotenv.dotenv_values('.env')\n")
    _write(tmp_path, "app/e.py", "import os as env\n\nvalue = env.getenv('A')\nother = env.environ['B']\n")
    _write(tmp_path, "app/clean.py", "# os.environ is only mentioned in a comment\nNAME = 'os.getenv'\n")
    report = checker.check_env_reads(tmp_path / "app", tmp_path, {})
    assert report.warnings == []
    flagged = sorted(message.split(":")[0] + ":" + message.split(":")[1] for message in report.errors)
    assert flagged == ["app/a.py:3", "app/a.py:4", "app/b.py:3", "app/c.py:3", "app/d.py:3", "app/e.py:3", "app/e.py:4"]


def test_env_read_allowlist_suppresses_errors_and_stale_entry_warns(tmp_path: Path) -> None:
    _write(tmp_path, "app/legacy.py", "import os\n\nvalue = os.getenv('A')\n")
    _write(tmp_path, "app/fixed.py", "value = 1\n")
    allowlist = {"app/legacy.py": (1, "pending B6"), "app/fixed.py": (1, "already migrated")}
    report = checker.check_env_reads(tmp_path / "app", tmp_path, allowlist)
    assert report.errors == []
    assert report.warnings == [
        "ENV_READ_ALLOWLIST entry 'app/fixed.py' no longer reads the environment; drop the entry"
    ]


def test_env_read_allowlist_cap_is_per_site_not_per_file(tmp_path: Path) -> None:
    _write(tmp_path, "app/legacy.py", "import os\n\nvalue = os.getenv('A')\nnew = os.getenv('B')\n")
    _write(tmp_path, "app/shrunk.py", "import os\n\nvalue = os.getenv('A')\n")
    allowlist = {"app/legacy.py": (1, "one read"), "app/shrunk.py": (2, "was two reads")}
    report = checker.check_env_reads(tmp_path / "app", tmp_path, allowlist)
    assert len(report.errors) == 1
    assert report.errors[0].startswith("app/legacy.py: 2 lines read the process environment (lines 3, 4), over")
    assert report.warnings == [
        "ENV_READ_ALLOWLIST caps 'app/shrunk.py' at 2 but only 1 lines read the environment; lower the cap to 1"
    ]


def test_live_env_read_allowlist_caps_are_not_exceeded() -> None:
    # Errors only: a read cleanup (PR B6) landing before its allowlist update must only warn.
    report = checker.check_env_reads(checker.APP_DIR, checker.ROOT, checker.ENV_READ_ALLOWLIST)
    assert report.errors == []


def test_env_read_syntax_error_is_reported_not_raised(tmp_path: Path) -> None:
    _write(tmp_path, "app/broken.py", "def (:\n")
    report = checker.check_env_reads(tmp_path / "app", tmp_path, {})
    assert len(report.errors) == 1
    assert report.errors[0].startswith("app/broken.py: cannot parse")


def test_env_example_rejects_non_bootstrap_tiers_even_when_commented() -> None:
    text = "# CODEX_LB_ALPHA=1\nCODEX_LB_GAMMA=2\n# CODEX_LB_BETA=3\n# CODEX_LB_UNKNOWN=4\nPORT=2455\n"
    assert checker.env_example_fields(text) == ["alpha", "gamma", "beta", "unknown"]
    report = checker.check_env_example(text, FIELDS, TIER_MAP)
    assert report.errors == [
        ".env.example mentions CODEX_LB_BETA (T3); only T0/T1 settings belong in the operator template"
    ]


def test_live_env_example_only_mentions_bootstrap_and_topology_settings() -> None:
    text = checker.ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    report = checker.check_env_example(text, Settings.model_fields, SETTING_TIERS)
    assert report.errors == []


def test_field_budget_ratchet() -> None:
    assert checker.check_field_budget(10, 10).errors == []
    over = checker.check_field_budget(11, 10)
    assert len(over.errors) == 1
    assert "11 fields, over the [settings_fields] budget of 10" in over.errors[0]


def test_settings_field_budget_requires_section() -> None:
    assert checker.settings_field_budget("[settings_fields]\nmax = 7\n") == 7
    with pytest.raises(ValueError, match=r"\[settings_fields\]\.max"):
        checker.settings_field_budget("[readme]\nmax_lines = 1\n")


def test_live_settings_count_is_within_budget() -> None:
    budget = checker.settings_field_budget(checker.BUDGETS_PATH.read_text(encoding="utf-8"))
    assert checker.check_field_budget(len(Settings.model_fields), budget).errors == []
