from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS_DIR = REPO_ROOT / "app" / "db" / "alembic" / "versions"


def _load_checker_module() -> ModuleType:
    script_path = REPO_ROOT / "scripts" / "check_migration_topology.py"
    spec = importlib.util.spec_from_file_location("check_migration_topology", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.modules.get(spec.name) is module:
            del sys.modules[spec.name]
    return module


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    return _load_checker_module()


def _write_revision(versions_dir: Path, revision: str, down_revision: str | tuple[str, ...] | None) -> Path:
    versions_dir.mkdir(parents=True, exist_ok=True)
    if down_revision is None:
        rendered = "None"
    elif isinstance(down_revision, str):
        rendered = repr(down_revision)
    else:
        rendered = repr(tuple(down_revision))
    path = versions_dir / f"{revision}.py"
    path.write_text(
        f'"""fixture revision {revision}."""\n\n'
        "from __future__ import annotations\n\n"
        f"revision = {revision!r}\n"
        f"down_revision = {rendered}\n"
        "branch_labels = None\n"
        "depends_on = None\n\n\n"
        "def upgrade() -> None:\n    pass\n\n\n"
        "def downgrade() -> None:\n    pass\n",
        encoding="utf-8",
    )
    return path


def _linear_fixture(checker: ModuleType, versions_dir: Path) -> str:
    """A two-revision base lineage whose head sits at the ratchet cutoff."""
    base = "20260901_000000_fixture_base"
    head = f"{checker.RATCHET_PREFIX}_fixture_head"
    _write_revision(versions_dir, base, None)
    _write_revision(versions_dir, head, base)
    return head


def _errors(reports: list) -> list[str]:
    return [message for report in reports for message in report.errors]


def _warnings(reports: list) -> list[str]:
    return [message for report in reports for message in report.warnings]


def test_real_tree_has_a_single_head_and_no_violations(checker: ModuleType) -> None:
    reports, summary = checker.run_all(versions_dir=VERSIONS_DIR, base_ref="")
    assert _errors(reports) == []
    assert _warnings(reports) == []
    assert "head: " in summary


def test_main_on_the_real_tree_exits_zero(checker: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert checker.main(["--base-ref", ""]) == 0
    assert "migration topology checks passed" in capsys.readouterr().out


def test_parsed_graph_matches_alembics_own_view(checker: ModuleType, tmp_path: Path) -> None:
    """The stdlib parser must not drift from the graph Alembic actually builds."""
    from alembic.script import ScriptDirectory

    from app.db.migrate import _build_alembic_config

    script = ScriptDirectory.from_config(_build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.sqlite'}"))
    alembic_graph = {}
    for revision in script.walk_revisions():
        down = revision.down_revision
        parents = (down,) if isinstance(down, str) else tuple(down or ())
        alembic_graph[revision.revision] = tuple(sorted(parents))

    parsed = {
        revision.revision: tuple(sorted(revision.down_revisions)) for revision in checker.load_graph(VERSIONS_DIR)
    }
    assert parsed == alembic_graph
    assert list(checker.graph_heads(checker.load_graph(VERSIONS_DIR))) == sorted(script.get_heads())


def test_two_head_graph_fails_and_names_both_heads(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    # Two revisions authored in parallel on top of the same parent: the incident shape.
    _write_revision(versions, f"{checker.RATCHET_PREFIX}_left_branch", head)
    _write_revision(versions, "20260912_000000_right_branch", head)

    reports, _ = checker.run_all(versions_dir=versions, base_ref="")
    errors = _errors(reports)
    assert any(message.startswith("alembic_head_count_invalid expected=1 actual=2") for message in errors)
    fork = next(message for message in errors if message.startswith("alembic_head_count_invalid"))
    assert f"{checker.RATCHET_PREFIX}_left_branch" in fork
    assert "20260912_000000_right_branch" in fork
    assert f"down_revision={head}" in fork
    assert "MultipleHeads" in fork
    assert checker.main(["--versions-dir", str(versions), "--base-ref", ""]) == 1


def test_timestamp_prefix_collision_on_different_parents_reports_the_fork(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    _write_revision(versions, "20260912_000000_first_slot", head)
    # Same slot, different parent: a fork that no single branch can see.
    _write_revision(versions, "20260912_000000_second_slot", "20260901_000000_fixture_base")

    reports, _ = checker.run_all(versions_dir=versions, base_ref="")
    collisions = [message for message in _errors(reports) if message.startswith("alembic_timestamp_prefix_collision")]
    assert len(collisions) == 1
    message = collisions[0]
    assert "prefix=20260912_000000 count=2" in message
    assert "20260912_000000_first_slot (down_revision=" in message
    assert "20260912_000000_second_slot (down_revision=" in message
    assert "sit on different lineages" in message
    assert "MultipleHeads" in message


def test_timestamp_prefix_collision_when_chained_reports_lost_ordering(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    _write_revision(versions, "20260912_000000_first_slot", head)
    _write_revision(versions, "20260912_000000_second_slot", "20260912_000000_first_slot")

    reports, _ = checker.run_all(versions_dir=versions, base_ref="")
    collisions = [message for message in _errors(reports) if message.startswith("alembic_timestamp_prefix_collision")]
    assert len(collisions) == 1
    assert "they are chained" in collisions[0]
    # Chaining keeps one head, so only the collision rule can see this.
    assert not any(message.startswith("alembic_head_count_invalid") for message in _errors(reports))


def test_prefix_collision_before_the_ratchet_is_grandfathered(checker: ModuleType, tmp_path: Path) -> None:
    """Existing history keeps its 36 collision groups; only new slots are enforced."""
    versions = tmp_path / "versions"
    _write_revision(versions, "20260801_000000_old_base", None)
    _write_revision(versions, "20260802_000000_old_left", "20260801_000000_old_base")
    _write_revision(versions, "20260802_000000_old_right", "20260802_000000_old_left")

    reports, _ = checker.run_all(versions_dir=versions, base_ref="")
    assert not any(message.startswith("alembic_timestamp_prefix_collision") for message in _errors(reports))


def test_revision_id_must_match_its_filename(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    renamed = versions / "20260912_000000_renamed_file.py"
    _write_revision(versions, "20260912_000000_original_id", head).rename(renamed)

    reports, _ = checker.run_all(versions_dir=versions, base_ref="")
    assert any(
        message.startswith("alembic_revision_filename_mismatch revision=20260912_000000_original_id")
        for message in _errors(reports)
    )


def test_revision_id_format_is_enforced_for_every_revision(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    _write_revision(versions, "NotATimestamp", head)

    reports, _ = checker.run_all(versions_dir=versions, base_ref="")
    assert any(
        message.startswith("alembic_revision_id_format_invalid revision=NotATimestamp") for message in _errors(reports)
    )


def test_order_inversion_warns_but_does_not_fail(
    checker: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    versions = tmp_path / "versions"
    base = "20260901_000000_fixture_base"
    parent = "20260930_000000_fixture_parent"
    _write_revision(versions, base, None)
    _write_revision(versions, parent, base)
    # Authored earlier, re-pointed at a newer parent after main moved.
    _write_revision(versions, f"{checker.RATCHET_PREFIX}_rebased_child", parent)

    reports, _ = checker.run_all(versions_dir=versions, base_ref="")
    assert _errors(reports) == []
    assert any(message.startswith("alembic_revision_order_inverted") for message in _warnings(reports))
    assert checker.main(["--versions-dir", str(versions), "--base-ref", ""]) == 0
    assert "WARN: alembic_revision_order_inverted" in capsys.readouterr().out


def test_dangling_down_revision_and_second_base_are_errors(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    _write_revision(versions, "20260912_000000_dangling", "20260101_000000_does_not_exist")
    _write_revision(versions, "20260912_010000_second_base", None)
    _write_revision(versions, "20260912_020000_join", ("20260912_000000_dangling", "20260912_010000_second_base"))
    _write_revision(versions, "20260912_030000_tip", ("20260912_020000_join", head))

    errors = _errors(checker.run_all(versions_dir=versions, base_ref="")[0])
    assert any("names no revision in" in message for message in errors)
    assert any(message.startswith("alembic_base_count_invalid expected=1 actual=2") for message in errors)


def test_duplicate_revision_ids_are_reported(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    duplicate = versions / "20260912_000000_copy.py"
    _write_revision(versions, "20260912_000000_duplicate", head)
    duplicate.write_text((versions / "20260912_000000_duplicate.py").read_text(encoding="utf-8"), encoding="utf-8")

    errors = _errors(checker.run_all(versions_dir=versions, base_ref="")[0])
    assert any(
        message.startswith("alembic_revision_duplicate revision=20260912_000000_duplicate") for message in errors
    )


def test_depends_on_warns_that_the_guard_does_not_model_it(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    path = _write_revision(versions, "20260912_000000_dependent", head)
    path.write_text(
        path.read_text(encoding="utf-8").replace("depends_on = None", f"depends_on = {head!r}"),
        encoding="utf-8",
    )

    reports, _ = checker.run_all(versions_dir=versions, base_ref="")
    assert _errors(reports) == []
    assert any("depends_on is set" in message for message in _warnings(reports))


def test_unparsable_revision_is_a_config_error(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    _linear_fixture(checker, versions)
    (versions / "20260912_000000_broken.py").write_text("down_revision = None\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no module-level 'revision' assignment"):
        checker.load_graph(versions)
    assert checker.main(["--versions-dir", str(versions), "--base-ref", ""]) == 2


def test_disconnected_cycle_is_rejected(checker: ModuleType, tmp_path: Path) -> None:
    """One head and one base are not enough: a mutually-referencing pair hides between them."""
    versions = tmp_path / "versions"
    _linear_fixture(checker, versions)
    _write_revision(versions, "20260912_000000_cycle_left", "20260912_010000_cycle_right")
    _write_revision(versions, "20260912_010000_cycle_right", "20260912_000000_cycle_left")

    revisions = checker.load_graph(versions)
    assert len(checker.graph_heads(revisions)) == 1
    errors = _errors(checker.run_all(versions_dir=versions, base_ref="")[0])
    cycle = next(message for message in errors if message.startswith("alembic_revision_cycle"))
    assert "20260912_000000_cycle_left,20260912_010000_cycle_right" in cycle
    unreachable = next(message for message in errors if message.startswith("alembic_revision_unreachable"))
    assert "20260912_000000_cycle_left,20260912_010000_cycle_right" in unreachable
    assert checker.main(["--versions-dir", str(versions), "--base-ref", ""]) == 1


def test_cycle_inside_the_reachable_lineage_is_rejected(checker: ModuleType, tmp_path: Path) -> None:
    base = "20260901_000000_fixture_base"
    versions = tmp_path / "versions"
    _write_revision(versions, base, None)
    _write_revision(versions, "20260912_000000_cycle_left", (base, "20260912_010000_cycle_right"))
    _write_revision(versions, "20260912_010000_cycle_right", "20260912_000000_cycle_left")
    _write_revision(versions, "20260912_020000_tip", "20260912_000000_cycle_left")

    revisions = checker.load_graph(versions)
    assert checker.graph_heads(revisions) == ("20260912_020000_tip",)
    assert checker.unreachable_revisions(revisions) == ()
    errors = checker.check_graph_shape(revisions).errors
    cycle = next(message for message in errors if message.startswith("alembic_revision_cycle"))
    assert "20260912_000000_cycle_left" in cycle
    assert "20260912_010000_cycle_right" in cycle


def test_self_referencing_revision_is_rejected(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    _linear_fixture(checker, versions)
    _write_revision(versions, "20260912_000000_self_parent", "20260912_000000_self_parent")

    errors = checker.check_graph_shape(checker.load_graph(versions)).errors
    assert any("20260912_000000_self_parent" in message for message in errors if "alembic_revision_cycle" in message)


def test_lineage_without_a_base_is_rejected(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    _write_revision(versions, "20260912_000000_left", "20260912_010000_right")
    _write_revision(versions, "20260912_010000_right", "20260912_000000_left")

    errors = checker.check_graph_shape(checker.load_graph(versions)).errors
    assert any(message.startswith("alembic_base_count_invalid expected=1 actual=0") for message in errors)


def _base_revision(checker: ModuleType, revision: str, down_revision: str | tuple[str, ...] | None):
    if down_revision is None:
        parents: tuple[str, ...] = ()
    elif isinstance(down_revision, str):
        parents = (down_revision,)
    else:
        parents = tuple(down_revision)
    return checker.Revision(revision=revision, down_revisions=parents, filename=f"{revision}.py")


def test_branch_fork_is_reported_from_the_branch_alone(checker: ModuleType, tmp_path: Path) -> None:
    """The decisive case: the checkout does not contain the revision main added.

    Only the base ref's own ``down_revision`` edges can show that the branch's
    parent is no longer main's head, so the checkout below deliberately omits
    ``landed_on_main``.
    """
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    _write_revision(versions, "20260912_010000_branch_revision", head)
    base_revisions = [
        _base_revision(checker, "20260901_000000_fixture_base", None),
        _base_revision(checker, head, "20260901_000000_fixture_base"),
        _base_revision(checker, "20260912_000000_landed_on_main", head),
    ]

    revisions = checker.load_graph(versions)
    assert checker.check_graph_shape(revisions).errors == []  # one head on the branch alone
    report = checker.check_branch_fork(revisions, base_revisions, "origin/main")
    assert len(report.errors) == 1
    message = report.errors[0]
    assert message.startswith(
        f"alembic_branch_forks_base revision=20260912_010000_branch_revision parent={head} base_ref=origin/main"
    )
    assert "already builds on" in message
    assert "20260912_000000_landed_on_main" in message
    assert "head: 20260912_000000_landed_on_main" in message
    assert "two Alembic heads" in message


def test_branch_revision_on_the_base_head_is_accepted(checker: ModuleType, tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    _write_revision(versions, "20260912_000000_branch_revision", head)
    _write_revision(versions, "20260912_010000_stacked_revision", "20260912_000000_branch_revision")
    base_revisions = [
        _base_revision(checker, "20260901_000000_fixture_base", None),
        _base_revision(checker, head, "20260901_000000_fixture_base"),
    ]

    report = checker.check_branch_fork(checker.load_graph(versions), base_revisions, "origin/main")
    assert report.errors == []


def test_merge_revision_is_exempt_from_the_branch_fork_check(checker: ModuleType, tmp_path: Path) -> None:
    """Merging the two heads is the sanctioned repair; it must not be flagged."""
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    _write_revision(versions, "20260912_000000_landed_on_main", head)
    _write_revision(versions, "20260912_010000_other_head", head)
    _write_revision(
        versions,
        "20260912_020000_merge_heads",
        ("20260912_000000_landed_on_main", "20260912_010000_other_head"),
    )
    base_revisions = [
        _base_revision(checker, "20260901_000000_fixture_base", None),
        _base_revision(checker, head, "20260901_000000_fixture_base"),
        _base_revision(checker, "20260912_000000_landed_on_main", head),
        _base_revision(checker, "20260912_010000_other_head", head),
    ]

    revisions = checker.load_graph(versions)
    assert checker.check_branch_fork(revisions, base_revisions, "origin/main").errors == []
    assert checker.check_graph_shape(revisions).errors == []


def test_parent_unknown_to_the_base_ref_does_not_report_a_fork(checker: ModuleType, tmp_path: Path) -> None:
    """A parent the base ref does not carry is branch-local work, not a fork."""
    versions = tmp_path / "versions"
    head = _linear_fixture(checker, versions)
    _write_revision(versions, "20260912_000000_newer_revision", head)
    _write_revision(versions, "20260912_010000_branch_revision", "20260912_000000_newer_revision")
    base_revisions = [
        _base_revision(checker, "20260901_000000_fixture_base", None),
        _base_revision(checker, head, "20260901_000000_fixture_base"),
    ]

    report = checker.check_branch_fork(checker.load_graph(versions), base_revisions, "origin/main")
    assert report.errors == []


def test_base_ref_revisions_reads_the_graph_out_of_git(checker: ModuleType) -> None:
    """The git plumbing must reproduce the same edges as reading the working tree."""
    base_revisions = checker.base_ref_revisions("HEAD")
    assert base_revisions is not None
    from_git = {revision.revision: revision.down_revisions for revision in base_revisions}
    from_disk = {revision.revision: revision.down_revisions for revision in checker.load_graph(VERSIONS_DIR)}
    assert from_git == from_disk
    assert "__init__" not in from_git


def test_missing_base_ref_skips_the_branch_fork_check(checker: ModuleType) -> None:
    assert checker.base_ref_revisions("refs/heads/definitely-not-a-real-ref") is None
    _, summary = checker.run_all(versions_dir=VERSIONS_DIR, base_ref="refs/heads/definitely-not-a-real-ref")
    assert "base-ref check: skipped (ref unavailable)" in summary
