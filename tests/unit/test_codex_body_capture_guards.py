"""Refusals and naming of the isolated Codex body capture lane (#2123).

What this module must guarantee is that every refusal fires *before* a process
starts, because the whole safety argument of the lane ("no credentials, no
upstream, no quota") rests on those preconditions rather than on operator
discipline. Each guard gets a positive case and a refusing case; the origin the
run starts is covered next door, in ``test_codex_body_capture_origin.py``.

**What actually runs.** Nothing here starts ``codex``, a uvicorn thread or the
capture orchestration. ``unshare`` runs twice: once at *import*, because
``_unprivileged_netns_available`` probes it to decide a ``skipif`` (so it runs
under ``--collect-only`` and even when the test is deselected), and once inside
``test_the_observation_reports_loopback_only_inside_a_real_namespace``, which
runs it on purpose -- a verifier of namespace isolation that is never exercised
inside a namespace proves nothing.

That claim used to be false in a way only a particular host revealed: two tests
reached ``main`` past the isolation observation, and on a *loopback-only* host --
a container with no uplink, or a suite already running inside ``unshare --net``
-- ``main`` proceeded into the full orchestration, binding port 19090 and
executing ``--codex-bin`` twice, while the one test asserting the isolation
refusal skipped itself on that same host. The observation is now stubbed at the
module attribute wherever a test needs to reach or refuse it, so both the
contract above and the refusal hold on every host.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.core.utils.proxy_env import STANDARD_OUTBOUND_PROXY_ENV_NAMES
from scripts.traffic_analysis import codex_body_capture
from scripts.traffic_analysis.codex_body_capture import (
    CAPTURE_TOKEN_VARIABLE,
    DEFAULT_CATALOG,
    FORBIDDEN_ENVIRONMENT_PREFIXES,
    FORBIDDEN_ENVIRONMENT_VARIABLES,
    FORBIDDEN_PROXY_ENVIRONMENT_VARIABLES,
    LOOPBACK_INTERFACE_NAMES,
    PROVIDER_NAME,
    REEXEC_FLAG,
    REPO_ROOT,
    CaptureRefusal,
    CaptureTarget,
    assert_ambient_home_uncredentialed,
    assert_catalog_path,
    assert_clean_environment,
    assert_home_uncredentialed,
    assert_loopback_base_url,
    assert_output_outside_repo,
    body_summary,
    build_parser,
    capture_artifact_name,
    capture_config_toml,
    capture_environment,
    main,
    network_isolation,
    observed_network_interfaces,
    preflight,
    reexec_command,
    reexec_environment,
)

pytestmark = pytest.mark.unit

_STARTED_AT = datetime(2026, 9, 11, 17, 29, 52, tzinfo=UTC)

# The environment variable that used to carry "already inside the namespace".
# It appears in no source file any more, and this literal is what keeps it that
# way: an inherited export of it must not change what the run does.
_RETIRED_REEXEC_MARKER = "CODEX_BODY_CAPTURE_NETNS"


# --- assert_output_outside_repo -------------------------------------------------------


def test_output_outside_the_repository_is_accepted(tmp_path: Path) -> None:
    destination = tmp_path / "captures"

    # ``tmp_path`` is itself under ``/tmp``, so the temporary-filesystem roots
    # are injected rather than the real ones; the refusal is covered below.
    resolved = assert_output_outside_repo(destination, repo_root=tmp_path / "repo", forbidden_roots=("/nonexistent",))

    assert resolved == destination.resolve()


def test_output_inside_the_repository_is_refused() -> None:
    """A raw capture is never committed, so it may not even be written in the tree."""

    with pytest.raises(CaptureRefusal, match="outside the repository"):
        assert_output_outside_repo(REPO_ROOT / "scripts" / "traffic_analysis" / "output")


def test_output_under_a_temporary_filesystem_is_refused() -> None:
    """Storage policy, and Codex refuses to create helper binaries under a temporary dir."""

    with pytest.raises(CaptureRefusal, match="storage policy"):
        assert_output_outside_repo(Path("/tmp/codex-body-capture"))


def test_a_symlinked_output_directory_is_refused(tmp_path: Path) -> None:
    """Resolving first would let the symlink target escape the repository check."""

    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises(CaptureRefusal, match="symlink"):
        assert_output_outside_repo(link, repo_root=tmp_path / "repo", forbidden_roots=("/nonexistent",))


# --- assert_clean_environment ---------------------------------------------------------


def test_a_clean_environment_is_accepted() -> None:
    assert_clean_environment({"PATH": "/usr/bin", "HOME": "/home/someone"})


@pytest.mark.parametrize("variable", sorted(FORBIDDEN_ENVIRONMENT_VARIABLES))
def test_an_upstream_pointer_in_the_environment_is_refused(variable: str) -> None:
    with pytest.raises(CaptureRefusal, match=variable):
        assert_clean_environment({variable: "value"})


def test_a_deployment_configuration_prefix_in_the_environment_is_refused() -> None:
    variable = f"{FORBIDDEN_ENVIRONMENT_PREFIXES[0]}UPSTREAM_BASE_URL"

    with pytest.raises(CaptureRefusal, match=variable):
        assert_clean_environment({variable: "https://example.invalid"})


@pytest.mark.parametrize("spelling", [str.lower, str.upper])
@pytest.mark.parametrize("variable", sorted(FORBIDDEN_PROXY_ENVIRONMENT_VARIABLES))
def test_an_outbound_proxy_variable_in_the_environment_is_refused(variable: str, spelling: Any) -> None:
    """Measured, not assumed: the Codex client routes even a loopback POST through ``HTTP_PROXY``.

    A capture run with ``HTTP_PROXY`` set captures nothing, and under
    ``--no-network-namespace`` the whole request body goes to the named host.
    Both spellings are honoured by the client, so both must refuse.
    """

    name = spelling(variable)

    with pytest.raises(CaptureRefusal, match=name):
        assert_clean_environment({name: "http://127.0.0.1:1"})


def test_preflight_refuses_a_proxied_shell_before_creating_anything(tmp_path: Path) -> None:
    """The refusal has to fire from the command, not only from a unit call.

    Before it existed, a proxied shell reached the capture: the guard accepted
    it and ``capture_environment`` handed ``HTTP_PROXY``, ``https_proxy``,
    ``ALL_PROXY``, ``WS_PROXY`` and ``NO_PROXY`` straight to ``codex exec``,
    which routes even a ``http://127.0.0.1:<port>/v1`` POST through them.
    """

    catalog = tmp_path / "models_cache.json"
    catalog.write_text('{"models": []}', encoding="utf-8")
    destination = tmp_path / "captures"
    args = build_parser().parse_args(
        ["--model", "gpt-5.5", "--out", str(destination), "--catalog", str(catalog), "--codex-bin", "/usr/bin/true"]
    )
    proxied = {
        "PATH": "/usr/bin",
        "HTTP_PROXY": "http://proxy.invalid:8080",
        "https_proxy": "http://proxy.invalid:8080",
        "ALL_PROXY": "socks5://proxy.invalid:1080",
        "NO_PROXY": "localhost",
    }

    with pytest.raises(CaptureRefusal, match="ALL_PROXY, HTTP_PROXY, NO_PROXY, https_proxy"):
        preflight(args, proxied)

    assert not destination.exists()


def test_the_refused_proxy_family_covers_the_names_production_reads() -> None:
    """Drift guard against ``app.core.utils.proxy_env``, the other end of the same surface."""

    assert FORBIDDEN_PROXY_ENVIRONMENT_VARIABLES >= set(STANDARD_OUTBOUND_PROXY_ENV_NAMES)
    assert "no_proxy" in FORBIDDEN_PROXY_ENVIRONMENT_VARIABLES
    assert all(name == name.casefold() for name in FORBIDDEN_PROXY_ENVIRONMENT_VARIABLES)


# --- assert_home_uncredentialed -------------------------------------------------------


def test_a_throwaway_home_is_accepted(tmp_path: Path) -> None:
    assert_home_uncredentialed(tmp_path)


def test_a_credentialed_home_is_refused(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")

    with pytest.raises(CaptureRefusal, match="credentialed CODEX_HOME"):
        assert_home_uncredentialed(tmp_path)


def test_an_exported_credentialed_codex_home_is_refused(tmp_path: Path) -> None:
    """The reachable form of the guard: the run overwrites ``CODEX_HOME``, so it must refuse it.

    Checking only the throwaway home made the refusal unreachable by
    construction -- ``tempfile.mkdtemp`` had created it three lines earlier, so
    no ``auth.json`` could exist -- and an operator who exported a credentialed
    home was silently ignored rather than refused.
    """

    (tmp_path / "auth.json").write_text('{"tokens": {}}', encoding="utf-8")

    with pytest.raises(CaptureRefusal, match="credentialed CODEX_HOME"):
        assert_ambient_home_uncredentialed({"CODEX_HOME": str(tmp_path)})


def test_an_exported_uncredentialed_codex_home_is_accepted(tmp_path: Path) -> None:
    """Only credentials refuse: an exported home is otherwise just overwritten."""

    assert_ambient_home_uncredentialed({"CODEX_HOME": str(tmp_path)})
    assert_ambient_home_uncredentialed({"CODEX_HOME": ""})
    assert_ambient_home_uncredentialed({})


def test_preflight_refuses_an_exported_credentialed_home_before_creating_anything(tmp_path: Path) -> None:
    """The guard has to fire from the command, not only from a unit call."""

    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "auth.json").write_text('{"tokens": {}}', encoding="utf-8")
    catalog = tmp_path / "models_cache.json"
    catalog.write_text('{"models": []}', encoding="utf-8")
    destination = tmp_path / "captures"
    args = build_parser().parse_args(
        [
            "--model",
            "gpt-5.5",
            "--out",
            str(destination),
            "--catalog",
            str(catalog),
            "--codex-bin",
            "/usr/bin/true",
        ]
    )

    with pytest.raises(CaptureRefusal, match="credentialed CODEX_HOME"):
        preflight(args, {"PATH": "/usr/bin", "CODEX_HOME": str(home)})

    assert not destination.exists()


# --- assert_loopback_base_url ---------------------------------------------------------


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:19090/v1", "http://localhost:19090/v1", "http://[::1]:1/v1"])
def test_a_loopback_origin_is_accepted(base_url: str) -> None:
    assert_loopback_base_url(base_url)


@pytest.mark.parametrize(
    "base_url",
    ["http://10.0.0.113:19090/v1", "https://chatgpt.com/backend-api", "http://example.invalid/v1", "not-a-url"],
)
def test_a_non_loopback_origin_is_refused(base_url: str) -> None:
    with pytest.raises(CaptureRefusal, match="loopback HTTP"):
        assert_loopback_base_url(base_url)


# --- assert_catalog_path --------------------------------------------------------------


def test_a_catalog_file_is_accepted(tmp_path: Path) -> None:
    catalog = tmp_path / "models_cache.json"
    catalog.write_text('{"models": []}', encoding="utf-8")

    assert assert_catalog_path(catalog) == catalog.resolve()


def test_the_committed_default_catalog_is_accepted() -> None:
    """``--catalog`` has a committed default, so the "one command" needs no arguments beyond --out."""

    assert assert_catalog_path(DEFAULT_CATALOG) == DEFAULT_CATALOG.resolve()
    assert build_parser().parse_args(["--model", "gpt-5.5", "--out", "/mnt/scratch/tmp/x"]).catalog == DEFAULT_CATALOG


def test_a_missing_catalog_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CaptureRefusal, match="not found"):
        assert_catalog_path(tmp_path / "absent.json")


def test_an_environment_file_as_catalog_is_refused(tmp_path: Path) -> None:
    secrets = tmp_path / ".env.production"
    secrets.write_text("TOKEN=value\n", encoding="utf-8")

    with pytest.raises(CaptureRefusal, match="environment file"):
        assert_catalog_path(secrets)


def test_repository_configuration_as_catalog_is_refused() -> None:
    with pytest.raises(CaptureRefusal, match="repository configuration|not found"):
        assert_catalog_path(REPO_ROOT / "config" / "settings.json")


# --- naming, configuration and the run environment ------------------------------------


def test_artifact_names_are_keyed_by_slug_transport_and_run_start() -> None:
    """The prototype's per-process counter collided across runs."""

    standard = capture_artifact_name("body", CaptureTarget("gpt-5.5", "http"), _STARTED_AT)
    lite = capture_artifact_name("body", CaptureTarget("gpt-5.6-sol", "websocket"), _STARTED_AT)

    assert standard == "body-gpt-5.5-http-20260911T172952Z.json"
    assert lite == "body-gpt-5.6-sol-websocket-20260911T172952Z.json"
    assert standard != lite


def test_a_path_separator_in_a_slug_cannot_escape_the_capture_directory(tmp_path: Path) -> None:
    name = capture_artifact_name("body", CaptureTarget("../../etc/passwd", "http"), _STARTED_AT)

    assert "/" not in name and ".." not in name
    assert (tmp_path / name).resolve().parent == tmp_path.resolve()


@pytest.mark.parametrize(
    ("transport", "expected"),
    [("http", "supports_websockets = false"), ("websocket", "supports_websockets = true")],
)
def test_the_generated_configuration_names_only_the_loopback_provider(transport: str, expected: str) -> None:
    config = capture_config_toml(model_slug="gpt-5.5", base_url="http://127.0.0.1:19090/v1", transport=transport)

    assert f'model_provider = "{PROVIDER_NAME}"' in config
    assert "requires_openai_auth = false" in config
    assert f'env_key = "{CAPTURE_TOKEN_VARIABLE}"' in config
    assert expected in config
    # Zero retries: a mistake fails the run instead of quietly capturing twice.
    assert "request_max_retries = 0" in config and "stream_max_retries = 0" in config


def test_the_child_environment_drops_upstream_pointers_and_names_the_throwaway_home(tmp_path: Path) -> None:
    parent = {
        "PATH": "/usr/bin",
        # Assembled rather than written as a literal, so a repository secret
        # scanner does not spend a review cycle on a fake.
        "OPENAI_API_KEY": "sk-" + "live-should-not-propagate",
        "CODEX_LB_UPSTREAM_BASE_URL": "https://example.invalid",
        "CODEX_HOME": "/home/someone/.codex",
    }

    child = capture_environment(parent, home=tmp_path)

    assert_clean_environment(child)
    assert child["CODEX_HOME"] == str(tmp_path)
    assert child[CAPTURE_TOKEN_VARIABLE]
    assert child["PATH"] == "/usr/bin"


def test_the_child_environment_never_inherits_an_outbound_proxy_variable(tmp_path: Path) -> None:
    """The spec's second half: the child process it would have started never inherits one."""

    parent = {
        "PATH": "/usr/bin",
        "HTTP_PROXY": "http://proxy.invalid:8080",
        "https_proxy": "http://proxy.invalid:8080",
        "ALL_PROXY": "socks5://proxy.invalid:1080",
        "NO_PROXY": "localhost",
    }

    child = capture_environment(parent, home=tmp_path)

    assert [name for name in child if name.casefold().endswith("_proxy")] == []
    assert_clean_environment(child)


def test_the_namespace_reexec_keeps_this_repository_first_on_the_import_path() -> None:
    """The child is started by file path, so ``site-packages`` would otherwise win.

    With an installed copy of this project on the path, the re-exec imported
    *that* checkout's ``scripts.traffic_analysis.origin_fixture`` and died with
    ``ImportError: cannot import name 'decode_request_body'`` -- the documented
    ``python -m`` invocation working before the re-exec and failing after it.
    """

    pinned = reexec_environment({"PATH": "/usr/bin"})
    appended = reexec_environment({"PYTHONPATH": "/elsewhere"})

    assert pinned["PYTHONPATH"] == str(REPO_ROOT)
    assert appended["PYTHONPATH"] == f"{REPO_ROOT}{os.pathsep}/elsewhere"
    # The child is told it is inside the namespace on its command line, never
    # through an inheritable variable.
    assert _RETIRED_REEXEC_MARKER not in pinned


# --- the network-namespace attestation ------------------------------------------------


@cache
def _unprivileged_netns_available() -> bool:
    if shutil.which("unshare") is None or shutil.which("ip") is None:
        return False
    probe = subprocess.run(
        ["unshare", "--map-root-user", "--net", "true"],
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _capture_argv(destination: Path, catalog: Path, *extra: str) -> list[str]:
    """The documented command line, minus the flags a test varies."""

    return [
        "--model",
        "gpt-5.5",
        "--out",
        str(destination),
        "--catalog",
        str(catalog),
        "--codex-bin",
        "/usr/bin/true",
        *extra,
    ]


@pytest.fixture
def reachable_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """An ``--out`` and a catalog that satisfy preflight, so a test can reach the re-exec.

    ``tmp_path`` is under ``/tmp``, which the storage policy refuses; that
    refusal has its own tests above and is pinned away here rather than
    reimplemented.
    """

    monkeypatch.setattr(codex_body_capture, "FORBIDDEN_OUTPUT_ROOTS", ("/nonexistent",))
    catalog = tmp_path / "models_cache.json"
    catalog.write_text('{"models": [{"slug": "gpt-5.5"}]}', encoding="utf-8")
    return tmp_path / "captures", catalog


def test_an_inherited_marker_cannot_skip_the_namespace_reexec(
    reachable_capture: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observed through ``main``: the run re-executes, it does not capture unisolated.

    Reproduced twice before the fix. ``CODEX_BODY_CAPTURE_NETNS=1`` exported in
    the shell -- a leftover from a previous debugging session, or from a nested
    invocation -- made ``main`` skip the ``unshare`` re-exec entirely, run the
    capture on the host network, and still print ``loopback only`` and record
    ``network_namespace: true`` in the manifest. The marker now travels on the
    command line, where nothing can inherit it.
    """

    destination, catalog = reachable_capture
    monkeypatch.setenv(_RETIRED_REEXEC_MARKER, "1")
    reexecs: list[list[str]] = []
    monkeypatch.setattr(
        codex_body_capture,
        "_reexec_in_network_namespace",
        lambda argv: (reexecs.append(list(argv)), 0)[1],
    )
    argv = _capture_argv(destination, catalog)

    exit_code = main(argv, environment={"PATH": "/usr/bin"})

    assert exit_code == 0
    assert reexecs == [argv], "the run captured without re-executing into a network namespace"
    assert not destination.exists(), "the unisolated parent process must capture nothing"


def test_the_reexec_child_is_told_on_its_command_line_and_does_not_loop(
    reachable_capture: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The flag the parent appends is the one that stops the child re-executing.

    ``network_isolation`` is stubbed to refuse, which is how this test keeps the
    module's "pure functions only" contract *on every host*. Without the stub the
    run only stopped because the machine had a routable interface: on a
    loopback-only host -- a CI container with no uplink, or anyone running the
    suite inside ``unshare --net`` -- ``main`` walked straight past the
    observation into the full orchestration, binding port 19090 with a uvicorn
    thread and executing ``--codex-bin`` twice.
    """

    destination, catalog = reachable_capture
    command = reexec_command(["--model", "gpt-5.5"], executable="/usr/bin/python3")

    assert command[:4] == ["unshare", "--map-root-user", "--net", "--"]
    assert "ip link set lo up" in " ".join(command)
    assert command[-1] == REEXEC_FLAG

    monkeypatch.setattr(
        codex_body_capture,
        "_reexec_in_network_namespace",
        lambda argv: pytest.fail(f"the child re-executed itself: {argv}"),
    )
    monkeypatch.setattr(
        codex_body_capture,
        "observed_network_interfaces",
        lambda: ("eth0", "lo"),
    )

    exit_code = main(_capture_argv(destination, catalog, REEXEC_FLAG), environment={"PATH": "/usr/bin"})

    assert exit_code == 2
    assert "network isolation is not in effect" in capsys.readouterr().err
    assert not destination.exists(), "the child must start nothing before the observation clears it"


@pytest.mark.parametrize(
    "interfaces",
    [("eth0", "lo"), ("lo", "wlan0"), ("eth0",), ("docker0", "eth0", "lo")],
    ids=["ethernet", "wireless", "no-loopback", "bridge"],
)
def test_the_attestation_reads_the_running_namespace(
    interfaces: tuple[str, ...],
    reachable_capture: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run that reaches a routable interface refuses instead of attesting isolation.

    The observation is stubbed rather than taken from the machine, because the
    version that read the real host *skipped itself* on exactly the host where
    the refusal matters most: a loopback-only box, where the whole capture would
    otherwise proceed. ``REEXEC_FLAG`` alone -- the state an inherited marker
    used to produce -- must never buy the attestation, on any host.
    """

    destination, catalog = reachable_capture
    monkeypatch.setattr(codex_body_capture, "observed_network_interfaces", lambda: interfaces)

    exit_code = main(_capture_argv(destination, catalog, REEXEC_FLAG), environment={"PATH": "/usr/bin"})

    assert exit_code == 2
    assert "network isolation is not in effect" in capsys.readouterr().err
    assert not destination.exists()


def test_the_observation_answers_for_the_host_it_runs_on() -> None:
    """Unstubbed, and never skipped: the kernel's own answer includes loopback.

    The interface *list* is host-dependent, so what is asserted is the property
    that holds on every host -- the observation returns something, and loopback
    is in it -- while the refusal it feeds is asserted above against a fixed
    list. Together those two never leave a host on which neither runs.
    """

    observed = observed_network_interfaces()

    assert observed
    assert set(observed) & LOOPBACK_INTERFACE_NAMES, observed


def test_the_attestation_records_the_interfaces_it_observed() -> None:
    """The manifest field is evidence, not an echo of ``--no-network-namespace``."""

    isolated = network_isolation(requested=True, interfaces=("lo",))
    assert isolated == {"requested": True, "loopback_only": True, "interfaces": ["lo"]}

    with pytest.raises(CaptureRefusal, match="network isolation is not in effect"):
        network_isolation(requested=True, interfaces=("eth0", "lo"))

    opted_out = network_isolation(requested=False, interfaces=("eth0", "lo"))
    assert opted_out == {"requested": False, "loopback_only": False, "interfaces": ["eth0", "lo"]}


@pytest.mark.skipif(
    not _unprivileged_netns_available(), reason="unprivileged network namespaces are unavailable on this host"
)
def test_the_observation_reports_loopback_only_inside_a_real_namespace() -> None:
    """The verifier is exercised where it matters: inside the namespace the lane creates.

    ``/sys/class/net`` still lists the host's interfaces in here, because
    ``unshare --net`` creates no mount namespace and sysfs stays bound to the one
    it was mounted in. ``socket.if_nameindex`` asks the kernel over netlink and
    answers for this namespace, which is why the observation uses it.
    """

    if observed_network_interfaces() == ("lo",):  # pragma: no cover - the suite is already isolated
        pytest.skip("this host is already loopback-only, so the comparison would be vacuous")
    snippet = (
        "import json;"
        "from scripts.traffic_analysis.codex_body_capture import observed_network_interfaces;"
        "print(json.dumps(list(observed_network_interfaces())))"
    )
    command: Sequence[str] = [
        "unshare",
        "--map-root-user",
        "--net",
        "--",
        "sh",
        "-c",
        'ip link set lo up && exec "$@"',
        "sh",
        sys.executable,
        "-c",
        snippet,
    ]

    completed = subprocess.run(
        command,
        env=reexec_environment(os.environ),
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )

    assert json.loads(completed.stdout.strip().splitlines()[-1]) == ["lo"]


def test_the_body_summary_reports_the_facts_an_operator_checks() -> None:
    summary = body_summary(
        {
            "model": "gpt-5.5",
            "instructions": "base",
            "tools": [{"type": "function"}, {"type": "tool_search"}, {"type": "web_search"}],
            "input": [{"type": "message", "role": "developer"}, {"type": "message", "role": "user"}],
        }
    )

    assert summary["tool_types"] == ["function", "tool_search", "web_search"]
    assert summary["item_sequence"] == ["message/developer", "message/user"]
    assert summary["instructions_present"] is True


def test_the_body_summary_reports_an_absent_instructions_key() -> None:
    """The Lite lane's distinguishing fact, and the one the synthetic pair hid."""

    summary = body_summary({"model": "gpt-5.6-sol", "input": [{"type": "additional_tools", "role": "developer"}]})

    assert summary["instructions_present"] is False
    assert summary["tool_types"] is None
    assert summary["item_sequence"] == ["additional_tools/developer"]
