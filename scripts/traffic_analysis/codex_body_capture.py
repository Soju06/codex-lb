"""Capture a real Codex Responses request body with no upstream contact.

One command. No ChatGPT credentials, no quota, no network egress:

    uv run python -m scripts.traffic_analysis.codex_body_capture \
      --model gpt-5.5 --model gpt-5.6-sol --transport http \
      --out /mnt/scratch/tmp/codex-body-capture-$(date -u +%Y%m%d)

How it stays isolated:

* **Kernel-enforced, and verified by observation.** The script re-executes
  itself inside an unprivileged network namespace (``unshare --map-root-user
  --net`` plus ``ip link set lo up``), then asks the kernel which interfaces
  that namespace has and refuses to capture unless loopback is the only one. "No
  upstream Codex call" is a property of the sandbox that the run measures, not a
  restatement of the flags it was given.
* **No credentials.** A throwaway ``CODEX_HOME`` and a provider with
  ``requires_openai_auth = false`` and a disposable ``env_key`` token. The
  script refuses to start if an exported ``CODEX_HOME`` holds an ``auth.json``
  or if the environment carries any production/proxy variable.
* **Captured in the origin, not in a proxy.** The loopback origin receives
  plaintext HTTP and persists the decoded request bytes itself, reusing
  ``origin_fixture.decode_request_body``. No TLS, no mitmproxy addon and no
  ``uvx`` capture boundary -- the parity lane's three moving parts.

It does need the project environment, which is what ``uv run`` above supplies:
the origin is FastAPI plus uvicorn, and the request decoder is ``zstandard``.
A bare interpreter fails at import, and on many hosts ``python`` is not even a
command. Nothing here needs the application configured, only importable: this
module reads no settings and touches no database.

Why the catalog must be pinned: the Codex model manager invalidates its cache
on a ``client_version`` mismatch and caches for 300 s, so a capture run always
refetches ``/models`` from whatever base URL the provider names. The origin
therefore serves a catalog file and records its digest as provenance.
``--catalog`` defaults to ``DEFAULT_CATALOG``, the committed reference catalog
that produced the fixture corpus; its digest is the ``catalog_sha256`` those
provenance entries record, so any operator can reproduce a capture and verify
the recorded digest. To capture against a different model set, point
``--catalog`` at a Codex ``/models`` response -- the shape Codex itself caches
as ``$CODEX_HOME/models_cache.json``, an object with a ``models`` array whose
entries the client deserialises strictly. The catalog does not fully determine
the body: 0.154.0 layers bundled ``model_info`` overrides on top of it (a row
saying ``supports_search_tool: false`` still yields ``web_search`` and
``tool_search`` declarations), which is why the CLI version is the primary
provenance key and the catalog digest is secondary.

What CI covers: the guards and the naming are pure functions
(``tests/unit/test_codex_body_capture_guards.py``), and the origin is driven
over both transports with a test client
(``tests/unit/test_codex_body_capture_origin.py``). Only the orchestration -- the
namespace re-exec, the uvicorn thread and ``codex`` itself -- stays outside CI.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

try:
    from scripts.traffic_analysis.artifacts import atomic_write_json, atomic_write_text, file_attestation
    from scripts.traffic_analysis.origin_fixture import (
        decode_request_body,
        loopback_host,
        response_events,
        sse_frames,
    )
except ModuleNotFoundError:  # Allow direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.artifacts import atomic_write_json, atomic_write_text, file_attestation
    from scripts.traffic_analysis.origin_fixture import (
        decode_request_body,
        loopback_host,
        response_events,
        sse_frames,
    )

REPO_ROOT = Path(__file__).resolve().parents[2]
# The committed reference catalog, byte-identical to the one that produced the
# fixtures in ``tests/fixtures/codex_bodies`` -- its SHA-256 is the
# ``catalog_sha256`` those provenance entries record, pinned by the corpus gate.
# Without it ``--catalog`` was a required flag with no documented source, no
# schema and no sample, so nobody but the original operator could reproduce a
# capture or verify the recorded digest.
DEFAULT_CATALOG = Path(__file__).with_name("catalogs") / "codex-models-20260911.json"
DEFAULT_PORT = 19090
DEFAULT_PROMPT = "Return exactly CAPTURE_OK.\n"
CAPTURE_TOKEN_VARIABLE = "CODEX_CAPTURE_TOKEN"
CAPTURE_TOKEN_VALUE = "non-secret-capture-token"
PROVIDER_NAME = "capture-origin"
# How the re-executed child learns it is already inside the namespace. It is an
# argument rather than an environment variable on purpose: the previous marker
# (``CODEX_BODY_CAPTURE_NETNS=1``) was inherited from the operator's shell, so a
# stale export skipped the re-exec while the run still printed and recorded
# "loopback only". An argv flag cannot be exported, and the attestation below is
# an observation of the namespace rather than a restatement of this flag.
REEXEC_FLAG = "--already-in-network-namespace"
# Loopback under either spelling the platforms use.
LOOPBACK_INTERFACE_NAMES: frozenset[str] = frozenset({"lo", "lo0"})
# A container host has dozens of bridges and veth pairs; the refusal names a few
# and counts the rest rather than printing a wall of them.
_REPORTED_INTERFACE_LIMIT = 5
TRANSPORTS = ("http", "websocket")
# The websocket lane wraps the Responses body in a frame envelope; production
# builds the same one in ``_build_websocket_response_create_payload``.
WEBSOCKET_TURN_FRAME_TYPE = "response.create"

# Variables whose presence means a production or proxy configuration has bled
# into the shell. Mirrors ``traffic-parity-auth/env.sh``: a capture that picks
# up ``OPENAI_BASE_URL`` is no longer loopback-only, and one that picks up a
# ``CODEX_LB_*`` value is reading the operator's deployment.
FORBIDDEN_ENVIRONMENT_VARIABLES: frozenset[str] = frozenset(
    {
        "CHATGPT_BASE_URL",
        "CODEX_ACCESS_TOKEN",
        "CODEX_API_BASE_URL",
        "CODEX_SESSION_ID",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    }
)
FORBIDDEN_ENVIRONMENT_PREFIXES: tuple[str, ...] = ("CODEX_LB_",)

# The outbound proxy family, matched case-insensitively because both spellings
# are honoured. These are not merely untidy: the Codex client routes even a
# ``http://127.0.0.1:<port>/v1`` POST through ``HTTP_PROXY`` and does not bypass
# loopback, so a capture run in a proxied shell captures nothing -- and under
# ``--no-network-namespace`` it delivers the whole request body (cwd, AGENTS.md,
# skills inventory, prompt) to whatever host the variable names. ``no_proxy`` is
# refused with the rest because it is the same configuration surface: its
# presence says a proxy configuration reached this shell.
#
# Pinned in the guard tests against ``app.core.utils.proxy_env``, which is where
# the production side of the same list lives; this module stays app-free so the
# tooling runs on a checkout without the application environment.
FORBIDDEN_PROXY_ENVIRONMENT_VARIABLES: frozenset[str] = frozenset(
    {
        "all_proxy",
        "ftp_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "socks_proxy",
        "ws_proxy",
        "wss_proxy",
    }
)

# Storage policy: raw captures are long-lived multi-hundred-KB artifacts, and a
# ``CODEX_HOME`` under ``/tmp`` also makes Codex refuse to create its helper
# binaries ("Refusing to create helper binaries under temporary dir").
FORBIDDEN_OUTPUT_ROOTS: tuple[str, ...] = ("/tmp", "/var/tmp", "/dev/shm")
SUGGESTED_OUTPUT_ROOTS: tuple[str, ...] = ("/mnt/scratch/tmp", "~/tmp")


class CaptureRefusal(RuntimeError):
    """A precondition failed. Raised before any process or server starts."""


@dataclass(frozen=True, slots=True)
class CaptureTarget:
    model_slug: str
    transport: str


def assert_output_outside_repo(
    destination: Path,
    *,
    repo_root: Path = REPO_ROOT,
    forbidden_roots: tuple[str, ...] = FORBIDDEN_OUTPUT_ROOTS,
) -> Path:
    """Resolve ``destination`` or refuse it.

    Refuses a path inside the repository (a capture is never committed raw), a
    symlink anywhere on the way (the resolved target would escape the check),
    and any location under a temporary filesystem.
    """

    if destination.is_symlink():
        raise CaptureRefusal(f"output directory must not be a symlink: {destination}")
    resolved = destination.expanduser().resolve()
    if resolved == repo_root.resolve() or repo_root.resolve() in resolved.parents:
        raise CaptureRefusal(
            f"output directory must live outside the repository: {resolved} is inside {repo_root}. "
            f"Use one of {', '.join(SUGGESTED_OUTPUT_ROOTS)}."
        )
    for root in forbidden_roots:
        root_path = Path(root)
        if resolved == root_path or root_path in resolved.parents:
            raise CaptureRefusal(
                f"output directory must not live under {root} (storage policy): {resolved}. "
                f"Use one of {', '.join(SUGGESTED_OUTPUT_ROOTS)}."
            )
    return resolved


def is_forbidden_variable(name: str) -> bool:
    """Whether ``name`` is an upstream pointer or an outbound proxy setting."""

    return (
        name in FORBIDDEN_ENVIRONMENT_VARIABLES
        or name.startswith(FORBIDDEN_ENVIRONMENT_PREFIXES)
        or name.casefold() in FORBIDDEN_PROXY_ENVIRONMENT_VARIABLES
    )


def assert_clean_environment(environment: Mapping[str, str]) -> None:
    """Refuse a shell carrying production, proxy or upstream configuration."""

    offenders = sorted(name for name in environment if is_forbidden_variable(name))
    if offenders:
        raise CaptureRefusal(
            "refusing to capture with production/proxy configuration in the environment: "
            f"{', '.join(offenders)}. Run from a clean shell."
        )


def assert_home_uncredentialed(home: Path) -> None:
    """Refuse a ``CODEX_HOME`` that holds ChatGPT credentials."""

    if (home / "auth.json").exists():
        raise CaptureRefusal(
            f"refusing to capture with a credentialed CODEX_HOME: {home / 'auth.json'} exists. "
            "The capture lane uses a throwaway home and a disposable provider token."
        )


def assert_ambient_home_uncredentialed(environment: Mapping[str, str]) -> None:
    """Refuse an exported ``CODEX_HOME`` that holds ChatGPT credentials.

    The run itself always uses a throwaway home, so the exported value is
    overwritten rather than read. That is precisely why it must refuse loudly
    instead of being silently ignored: an operator who exported a credentialed
    home is asking for a capture this lane does not perform, and the lane's whole
    claim is "no ChatGPT credentials". Checking only the throwaway home -- which
    ``tempfile.mkdtemp`` created moments earlier and which therefore can never
    hold an ``auth.json`` -- made the guard unreachable by construction.
    """

    home = environment.get("CODEX_HOME")
    if home:
        assert_home_uncredentialed(Path(home).expanduser())


def assert_loopback_base_url(base_url: str) -> None:
    """Refuse an origin the capture cannot prove is local."""

    parts = urlsplit(base_url)
    if parts.scheme != "http" or not parts.hostname or not loopback_host(parts.hostname):
        raise CaptureRefusal(f"capture origin base URL must be loopback HTTP: {base_url}")


def observed_network_interfaces() -> tuple[str, ...]:
    """Every network interface visible in *this* namespace, sorted.

    ``socket.if_nameindex()`` asks the kernel over netlink, so it answers for the
    calling task's network namespace. ``/sys/class/net`` does not: sysfs stays
    bound to the namespace it was mounted in, and inside ``unshare --net``
    (which creates no mount namespace) it still lists the host's ``eth0`` --
    measured on the development host, and the reason this is not a directory
    listing.
    """

    return tuple(sorted(name for _index, name in socket.if_nameindex()))


def _summarise_interfaces(names: Sequence[str]) -> str:
    listed = ", ".join(names[:_REPORTED_INTERFACE_LIMIT])
    remainder = len(names) - _REPORTED_INTERFACE_LIMIT
    return f"{listed} and {remainder} more" if remainder > 0 else listed


def network_isolation(*, requested: bool, interfaces: Sequence[str] | None = None) -> dict[str, Any]:
    """Observe the namespace and refuse if the isolation the run claims is absent.

    The manifest field this returns is evidence, not a restatement of the flags:
    it is derived from the interfaces the kernel reports here and now. Before
    that, ``network_namespace: true`` merely echoed "the operator did not pass
    ``--no-network-namespace``", so an inherited ``CODEX_BODY_CAPTURE_NETNS=1``
    skipped the ``unshare`` re-exec and the run still attested isolation --
    reproduced, and the reason the marker is now an argv flag.

    A namespace whose only interface is loopback cannot reach anything off the
    host, which is the property the lane sells; how it came about does not
    matter, so an operator already inside such a namespace passes too.
    """

    observed = observed_network_interfaces() if interfaces is None else tuple(sorted(interfaces))
    routable = tuple(name for name in observed if name not in LOOPBACK_INTERFACE_NAMES)
    if requested and routable:
        raise CaptureRefusal(
            "network isolation is not in effect: this namespace still carries "
            f"{_summarise_interfaces(routable)}, so external egress is possible. "
            "The capture never started. Re-run the documented command, or pass "
            "--no-network-namespace --i-accept-network-egress to capture without isolation."
        )
    return {"requested": requested, "loopback_only": not routable, "interfaces": list(observed)}


def assert_catalog_path(catalog: Path) -> Path:
    """Refuse a catalog that is really configuration or a secret file."""

    if catalog.is_symlink():
        raise CaptureRefusal(f"catalog must not be a symlink: {catalog}")
    resolved = catalog.expanduser().resolve()
    if not resolved.is_file():
        raise CaptureRefusal(f"catalog file not found: {resolved}")
    if resolved.name.startswith(".env") or resolved.suffix == ".env":
        raise CaptureRefusal(f"refusing to serve an environment file as a model catalog: {resolved}")
    if (REPO_ROOT / "config").resolve() in resolved.parents:
        raise CaptureRefusal(f"refusing to serve repository configuration as a model catalog: {resolved}")
    return resolved


def capture_artifact_name(kind: str, target: CaptureTarget, started_at: datetime) -> str:
    """Filename keyed by slug, transport and run start, so runs never collide.

    The prototype numbered bodies with a per-process counter, which collided
    across runs. A dot survives (``gpt-5.5``) but a parent reference does not:
    the slug reaches this function straight from ``--model``.
    """

    slug = "".join(character if character.isalnum() or character in "-." else "-" for character in target.model_slug)
    while ".." in slug:
        slug = slug.replace("..", "-")
    return f"{kind}-{slug}-{target.transport}-{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"


def capture_config_toml(*, model_slug: str, base_url: str, transport: str) -> str:
    """The Codex configuration for one capture run.

    ``request_max_retries``/``stream_max_retries`` are zero so a mistake fails
    the run instead of quietly producing several bodies.
    """

    return "\n".join(
        (
            f'model = "{model_slug}"',
            f'model_provider = "{PROVIDER_NAME}"',
            "",
            f"[model_providers.{PROVIDER_NAME}]",
            'name = "openai"',
            f'base_url = "{base_url}"',
            'wire_api = "responses"',
            f'env_key = "{CAPTURE_TOKEN_VARIABLE}"',
            f"supports_websockets = {'true' if transport == 'websocket' else 'false'}",
            "requires_openai_auth = false",
            "request_max_retries = 0",
            "stream_max_retries = 0",
            "",
        )
    )


def capture_environment(environment: Mapping[str, str], *, home: Path) -> dict[str, str]:
    """The child environment: the caller's, minus upstream pointers, plus the throwaway home.

    Belt and braces with ``assert_clean_environment``: the refusal already ran,
    but the spec requires that the child process never inherits one of these
    even if a future caller reaches this function by another path.
    """

    child = {name: value for name, value in environment.items() if not is_forbidden_variable(name)}
    child["CODEX_HOME"] = str(home)
    child[CAPTURE_TOKEN_VARIABLE] = CAPTURE_TOKEN_VALUE
    return child


def body_summary(body: Mapping[str, Any]) -> dict[str, Any]:
    """The one-line facts an operator checks before sanitising."""

    tools = body.get("tools")
    input_items = body.get("input")
    return {
        "top_level_keys": sorted(body),
        "instructions_present": "instructions" in body,
        "tool_types": (
            sorted({str(tool.get("type")) for tool in tools if isinstance(tool, dict)})
            if isinstance(tools, list)
            else None
        ),
        "item_sequence": (
            [f"{item.get('type', '?')}/{item.get('role', '-')}" for item in input_items if isinstance(item, dict)]
            if isinstance(input_items, list)
            else None
        ),
    }


def is_turn_body(payload: Mapping[str, Any]) -> bool:
    """Whether a request carries the turn, rather than priming the context.

    Measured against codex-cli 0.154.0 in the isolated lane: HTTP sends exactly
    one POST, which is the turn. The websocket lane opens with a
    ``generate: false`` prewarm frame whose ``input`` is empty -- production
    knows the same marker in ``proxy/_service/http_bridge`` -- and only then
    sends the turn, which carries ``previous_response_id`` pointing at the
    primed response. Keeping "the first body" unconditionally would therefore
    persist an empty-transcript prewarm as the websocket capture.
    """

    if payload.get("generate") is False:
        return False
    items = payload.get("input")
    return bool(items) if isinstance(items, list | str) else False


def _build_origin(
    catalog: Path,
    destination: Path,
    started_at: datetime,
) -> tuple[FastAPI, list[dict[str, Any]]]:
    """The capture origin: serves the pinned catalog, persists decoded bodies.

    Both transports Codex can choose are served, because the provider config
    only *offers* websockets: the client decides, and a manifest that recorded
    the requested transport rather than the observed one would be a lie the day
    it falls back. Every artifact -- filename, record and manifest row -- is
    keyed by the channel the body actually arrived on.

    ``Request`` must be resolvable from module globals: with deferred
    annotations, a function-local import leaves FastAPI unable to see the
    parameter as the request object and it answers ``422`` on a query
    parameter that was never sent.
    """

    catalog_payload = json.loads(catalog.read_text(encoding="utf-8"))
    captured: list[dict[str, Any]] = []
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.target = None

    def _persist(payload: dict[str, Any], decoded: bytes, headers: Mapping[str, str], channel: str) -> None:
        target: CaptureTarget = app.state.target
        observed = CaptureTarget(model_slug=target.model_slug, transport=channel)
        record = {"model_slug": observed.model_slug, "transport": channel, "extra_turn": True}
        body_path = destination / capture_artifact_name("body", observed, started_at)
        headers_path = destination / capture_artifact_name("headers", observed, started_at)
        if not is_turn_body(payload):
            # A context prewarm, not a turn. It is still evidence and is kept
            # under its own name: on the Responses-Lite websocket lane the
            # prewarm is where the ``additional_tools`` bundle travels, so the
            # turn frame alone would lose the tool surface entirely.
            prewarm_path = destination / capture_artifact_name("prewarm", observed, started_at)
            if prewarm_path.exists():
                captured.append({**record, "prewarm": True})
                return
            prewarm_path.write_bytes(decoded)
            captured.append(
                {
                    **record,
                    "prewarm": True,
                    "prewarm_body": file_attestation("prewarm", prewarm_path),
                    "prewarm_summary": body_summary(payload),
                }
            )
            return
        if body_path.exists():
            # A retry or a second turn: keep the first body, never overwrite it.
            captured.append(record)
            return
        body_path.write_bytes(decoded)
        atomic_write_json(headers_path, dict(headers))
        captured.append(
            {
                **record,
                "extra_turn": False,
                "body": file_attestation("body", body_path),
                "headers": file_attestation("headers", headers_path),
                "summary": body_summary(payload),
            }
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "codex-body-capture-origin"}

    @app.get("/models")
    @app.get("/v1/models")
    @app.get("/codex/models")
    @app.get("/backend-api/codex/models")
    async def models() -> JSONResponse:
        return JSONResponse(catalog_payload)

    @app.post("/v1/responses")
    @app.post("/codex/responses")
    @app.post("/backend-api/codex/responses")
    async def responses(request: Request) -> Response:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
        decoded = decode_request_body(bytes(raw), request.headers.get("content-encoding"))
        payload = json.loads(decoded)
        _persist(payload, decoded, request.headers, "http")
        events = response_events()
        if payload.get("stream") is True:
            return StreamingResponse(sse_frames(events), media_type="text/event-stream")
        return JSONResponse(events[-1]["response"])

    @app.websocket("/v1/responses")
    @app.websocket("/codex/responses")
    @app.websocket("/backend-api/codex/responses")
    async def responses_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                frame = await websocket.receive_text()
                payload = json.loads(frame)
                if not isinstance(payload, dict) or payload.get("type") != WEBSOCKET_TURN_FRAME_TYPE:
                    continue
                # Verbatim: the frame *is* the request body on this transport,
                # ``type`` envelope included. The sanitiser removes the envelope
                # on the way to a fixture; the capture does not rewrite bytes.
                _persist(payload, frame.encode("utf-8"), websocket.headers, "websocket")
                for event in response_events():
                    await websocket.send_json(event)
        except WebSocketDisconnect:
            return

    return app, captured


def reexec_environment(environment: Mapping[str, str], *, repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """The re-exec child's environment: this repository first on the import path.

    The child is started by file path, so its ``sys.path[0]`` is the script's own
    directory and an installed copy of this project in ``site-packages`` wins the
    ``scripts.traffic_analysis`` import -- silently running another checkout's
    origin, or failing outright when the two differ. Pinning the repository that
    was actually launched makes the documented ``python -m`` invocation behave
    the same after the namespace re-exec as before it.

    It carries no "already inside the namespace" marker: that travels as
    ``REEXEC_FLAG`` on the command line, because an environment variable is
    inherited from the operator's shell and a stale export silently turned the
    isolation off.
    """

    child = dict(environment)
    existing = child.get("PYTHONPATH")
    child["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing}" if existing else str(repo_root)
    return child


def reexec_command(argv: Sequence[str], *, executable: str = sys.executable) -> list[str]:
    """The ``unshare`` command line that re-runs this script inside a fresh netns."""

    inner = 'ip link set lo up && exec "$@"'
    return [
        "unshare",
        "--map-root-user",
        "--net",
        "--",
        "sh",
        "-c",
        inner,
        "sh",
        executable,
        str(Path(__file__).resolve()),
        *argv,
        REEXEC_FLAG,
    ]


def _reexec_in_network_namespace(argv: Sequence[str]) -> int:
    """Re-run this script with loopback as the only reachable network."""

    if shutil.which("unshare") is None or shutil.which("ip") is None:
        raise CaptureRefusal(
            "unshare and ip are required for the isolated capture lane; "
            "pass --no-network-namespace --i-accept-network-egress to opt out"
        )
    return subprocess.run(reexec_command(argv), env=reexec_environment(os.environ), check=False).returncode


def _serve(app: Any, port: int) -> tuple[Any, threading.Thread]:
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", server_header=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="codex-body-capture-origin", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if server.started:
            return server, thread
        time.sleep(0.05)
    server.should_exit = True
    raise CaptureRefusal(f"capture origin did not start on 127.0.0.1:{port}")


def _run_codex(
    *,
    codex_binary: str,
    model_slug: str,
    workdir: Path,
    environment: Mapping[str, str],
    prompt: str,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    command = [
        codex_binary,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "-C",
        str(workdir),
        "-s",
        "read-only",
        "-m",
        model_slug,
        prompt,
    ]
    # ``codex exec`` blocks on "Reading additional input from stdin..." whenever
    # stdin is a pipe, so it must be closed rather than inherited.
    return subprocess.run(
        command,
        cwd=workdir,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture real Codex request bodies against a loopback origin.")
    parser.add_argument("--model", action="append", required=True, metavar="SLUG", help="Repeatable model slug")
    parser.add_argument("--transport", choices=TRANSPORTS, default="http")
    parser.add_argument("--out", type=Path, required=True, help="Capture directory outside the repository")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help=(
            "Pinned /models catalog served to Codex. Defaults to the committed reference catalog "
            f"({DEFAULT_CATALOG.relative_to(REPO_ROOT)}); a real one is Codex's own "
            "$CODEX_HOME/models_cache.json"
        ),
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--codex-bin", default=shutil.which("codex"))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--keep-home", action="store_true", help="Keep the throwaway CODEX_HOME for inspection")
    parser.add_argument("--no-network-namespace", action="store_true")
    parser.add_argument(
        "--i-accept-network-egress",
        action="store_true",
        help="Required companion of --no-network-namespace: egress is then only policy, not kernel",
    )
    # Internal: set by ``reexec_command`` on the child so it does not re-exec
    # again. Not an environment variable, and not load-bearing for the
    # attestation -- ``network_isolation`` observes the namespace either way.
    parser.add_argument(REEXEC_FLAG, action="store_true", help=argparse.SUPPRESS)
    return parser


def preflight(args: argparse.Namespace, environment: Mapping[str, str] | None = None) -> tuple[Path, Path, str]:
    """Every refusal, in order, before any directory, server or subprocess exists.

    ``environment`` is injectable so a test can assert the ordering against a
    known shell rather than against the one it happens to run in.
    """

    environment = os.environ if environment is None else environment
    if args.no_network_namespace and not args.i_accept_network_egress:
        raise CaptureRefusal("--no-network-namespace requires --i-accept-network-egress")
    if not args.codex_bin:
        raise CaptureRefusal("codex binary not found; pass --codex-bin")
    assert_clean_environment(environment)
    assert_ambient_home_uncredentialed(environment)
    # The storage policy is read from the module attribute rather than from the
    # default argument, so a test that has to reach the steps *after* this one
    # can pin it; the refusal itself is covered by its own tests.
    destination = assert_output_outside_repo(args.out, forbidden_roots=FORBIDDEN_OUTPUT_ROOTS)
    catalog = assert_catalog_path(args.catalog)
    base_url = f"http://127.0.0.1:{args.port}/v1"
    assert_loopback_base_url(base_url)
    return destination, catalog, base_url


def main(argv: Sequence[str] | None = None, environment: Mapping[str, str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(raw_argv)
    environment = os.environ if environment is None else environment
    try:
        destination, catalog, base_url = preflight(args, environment)
    except CaptureRefusal as exc:
        print(f"Refusing to capture: {exc}", file=sys.stderr)
        return 2

    if not args.no_network_namespace and not args.already_in_network_namespace:
        try:
            return _reexec_in_network_namespace(raw_argv)
        except CaptureRefusal as exc:
            print(f"Refusing to capture: {exc}", file=sys.stderr)
            return 2

    try:
        isolation = network_isolation(requested=not args.no_network_namespace)
    except CaptureRefusal as exc:
        print(f"Refusing to capture: {exc}", file=sys.stderr)
        return 2

    started_at = datetime.now(UTC)
    destination.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix="codex-capture-home-", dir=destination))
    workdir = Path(tempfile.mkdtemp(prefix="codex-capture-work-", dir=destination))
    try:
        atomic_write_text(
            workdir / "AGENTS.md",
            "Run the unit tests before declaring a change done.\n",
        )
        app, captured = _build_origin(catalog, destination, started_at)
        server, thread = _serve(app, args.port)
        print(
            "network isolation: "
            + ("loopback only" if isolation["loopback_only"] else "DISABLED by operator")
            + f" (observed interfaces: {_summarise_interfaces(isolation['interfaces'])})"
        )
        print(f"capture origin: {base_url} (health ok)")
        catalog_attestation = file_attestation("catalog", catalog)
        print(f"catalog: {catalog.name} sha256={catalog_attestation['sha256']}")
        codex_version = subprocess.run(
            [args.codex_bin, "--version"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        ).stdout.strip()
        print(f"codex: {codex_version}")

        runs: list[dict[str, Any]] = []
        for model_slug in args.model:
            target = CaptureTarget(model_slug=model_slug, transport=args.transport)
            app.state.target = target
            atomic_write_text(
                home / "config.toml",
                capture_config_toml(model_slug=model_slug, base_url=base_url, transport=args.transport),
            )
            child_environment = capture_environment(environment, home=home)
            try:
                completed = _run_codex(
                    codex_binary=args.codex_bin,
                    model_slug=model_slug,
                    workdir=workdir,
                    environment=child_environment,
                    prompt=args.prompt,
                    timeout_seconds=args.timeout_seconds,
                )
                exit_code: int | None = completed.returncode
                tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-3:]
            except subprocess.TimeoutExpired:
                exit_code = None
                tail = ["timed out"]
            record = next(
                (item for item in reversed(captured) if item["model_slug"] == model_slug and not item["extra_turn"]),
                None,
            )
            prewarm = next(
                (item for item in captured if item["model_slug"] == model_slug and item.get("prewarm_body")),
                None,
            )
            runs.append(
                {
                    "model_slug": model_slug,
                    # The record overwrites ``transport`` with the channel the
                    # body arrived on; the requested value only configured the
                    # provider, and remains here as the fallback for a run that
                    # captured nothing.
                    "transport": args.transport,
                    "transport_requested": args.transport,
                    "exit_code": exit_code,
                    **{key: value for key, value in (prewarm or {}).items() if key.startswith("prewarm")},
                    **(record or {}),
                }
            )
            if record is None:
                print(f"captured {model_slug:16s} {args.transport} NOTHING exit={exit_code} {' | '.join(tail)}")
                continue
            observed = str(record["transport"])
            attestation = record["body"]
            summary = record["summary"]
            print(
                f"captured {model_slug:16s} {observed} {attestation['bytes']} B "
                f"sha256={str(attestation['sha256'])[:16]}... exit={exit_code}"
                + ("" if observed == args.transport else f" (requested {args.transport})")
            )
            print(f"                 keys={','.join(summary['top_level_keys'])}")
            print(
                f"                 tools={summary['tool_types']} items={summary['item_sequence']} "
                f"instructions={'present' if summary['instructions_present'] else 'ABSENT'}"
            )
            if prewarm is not None:
                prewarm_summary = prewarm["prewarm_summary"]
                print(
                    f"                 prewarm {prewarm['prewarm_body']['bytes']} B "
                    f"tools={prewarm_summary['tool_types']} items={prewarm_summary['item_sequence']}"
                )
        server.should_exit = True
        thread.join(timeout=10.0)
        manifest = {
            "schema_version": 1,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "codex_version": codex_version,
            "transport_requested": args.transport,
            "transports_observed": sorted({str(run["transport"]) for run in runs if run.get("body")}),
            # Observed, not requested: the interface list the kernel reported
            # inside the namespace this run captured in.
            "network_isolation": isolation,
            "catalog": catalog_attestation,
            "runs": runs,
        }
        atomic_write_json(destination / "manifest.json", manifest)
        print(f"wrote: {destination}/{{body,headers,prewarm}}-*.json, manifest.json")
        print(
            "next: uv run python -m scripts.traffic_analysis.codex_body_sanitize "
            f"--in {destination}/body-<slug>-<transport>-<stamp>.json --out {destination}/fixture.json"
        )
        print("      then read it, and copy it in with --i-have-read-the-sanitised-body.")
        return 0 if all(run.get("body") for run in runs) else 1
    except CaptureRefusal as exc:
        print(f"Refusing to capture: {exc}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if not args.keep_home:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
