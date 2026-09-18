"""Residual scan over the committed Codex body fixtures.

**This is a second net, not the boundary.** The boundary is
``codex_body_sanitize``: a captured body is *rebuilt* from a structural
allowlist, so the only captured *strings* carried forward are the ones a rule
names a closed domain for. Numbers are a separate story and are bounded there
rather than matched here. What follows is a cheap backstop over the bytes that result -- it catches a
hand-edited fixture, a synthetic pre-strip fixture someone widened, and a
capture that was committed without going through the rebuild at all. It is
pattern matching, and pattern matching is incomplete; the last two rounds of
this tooling failed precisely by treating a green scan as a clearance.

Three passes over the same tree:

* **Credentials** -- ``privacy_scan.scan_tree`` unchanged, over every file. It
  already knows the shapes that must never be committed anywhere (token, API
  key, JWT, OAuth token fields).
* **Identifiers** -- over the fixture bodies only (``*.json``). The credential
  scanner has no vocabulary for a workspace path, a live UUID or a telemetry
  key, and it *passes* a tree full of them on its own.
* **Operator shapes** -- an absolute path, an account or host name, an
  environment-context tag or a skill inventory still holding a value the corpus
  does not declare. Every one of these is a shape, and each has a known way to
  be written that the shape does not match.

The identifier pass deliberately skips ``*.md``: the corpus README has to be
able to name the keys the rebuild removes, and prose is reviewed by a human, not
by shape. Credential shapes are still rejected in every file including the
README.

Ordering matters, and the previous version had it wrong. A finding that is
present in a *captured* body and absent from the rebuilt one says something
about the rebuild, never about the capture's safety --
``compare_captured_and_sanitised`` reports both sides and labels the difference
rather than letting the clean side stand in for a pass. The sanitiser CLI runs
that comparison on every rebuild and refuses to write when the rebuilt side is
not clean.

Every pattern is matched against the telemetry *form* rather than the bare word,
because the real Codex tool schema declares a legitimate numeric ``session_id``
parameter (the unified-exec session) -- a bare-word match would red-line a
genuine capture for the wrong reason. Findings never echo the offending value.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from scripts.traffic_analysis.artifacts import atomic_write_json
    from scripts.traffic_analysis.codex_body_sanitize import PLACEHOLDERS, Placeholders, is_placeholder_uuid
    from scripts.traffic_analysis.privacy_scan import scan_tree
except ModuleNotFoundError:  # Allow direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.traffic_analysis.artifacts import atomic_write_json
    from scripts.traffic_analysis.codex_body_sanitize import PLACEHOLDERS, Placeholders, is_placeholder_uuid
    from scripts.traffic_analysis.privacy_scan import scan_tree

_CHUNK_BYTES = 1024 * 1024
_OVERLAP_BYTES = 512

IDENTIFIER_SCAN_SUFFIXES: frozenset[str] = frozenset({".json"})

_UUID = re.compile(rb"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

# Telemetry keys that carry a *string* identifier. ``"session_id": {`` (a tool
# parameter schema) and ``session_id?: number`` (prose in a tool description)
# are both legitimate Codex tool vocabulary and must not match, which is why
# the pattern requires a quoted value -- and why a placeholder value is fine.
_STRING_VALUED_IDENTIFIER_KEYS: tuple[str, ...] = (
    "installation_id",
    "root_turn_id",
    "session_id",
    "thread_id",
    "turn_id",
    "window_id",
)

# Bare telemetry key names. Unlike every other kind this one cannot be
# conditioned on the value, so a fixture that exists to exercise the production
# telemetry stripper declares itself in ``provenance.json`` and is exempted by
# name rather than being made unrepresentative. The declaration is *read* from
# ``provenance.json`` (see ``declared_telemetry_key_bodies``) instead of being
# retyped on the command line: the documented gate step has to pass on a
# pristine checkout.
TELEMETRY_KEY_KIND = "telemetry_field"

PROVENANCE_NAME = "provenance.json"


# --- the operator shapes -------------------------------------------------------------
#
# These used to live in the sanitiser, where they drove an in-place rewrite of
# the captured text. That coupling is what produced the measured bypass: the
# rewrite turned ``PATH=/usr/local/bin:/home/jane/.local/bin`` into
# ``PATH=/workspace/repo:/home/jane/.local/bin`` and thereby deleted this gate's
# own trigger. Nothing rewrites a captured string any more, so the shapes are
# only ever read, here.

ENVIRONMENT_TAGS: tuple[tuple[str, str], ...] = (
    ("cwd", "workspace"),
    ("root", "workspace"),
    ("current_date", "date"),
    ("timezone", "timezone"),
    ("shell", "shell"),
)

# An absolute filesystem path, matched as a shape rather than as a prefix
# vocabulary: two or more segments, never preceded by a word character, ``/``,
# ``.`` or ``\``. That leaves a relative path (``tests/unit/x.py``), a URL
# (``https://example.com/a/b``), a prose enumeration (``cream/sand/tan``) and a
# ``../sibling`` reference unmatched. A segment is any run of characters that is
# not a separator, a text delimiter or a backslash, so a non-ASCII path
# (``/home/사용자/프로젝트``) matches like any other while a regex literal
# (``/\r?\n/``) and a JSON-escaped newline do not.
#
# ``:`` is *not* in the lookbehind, which is a measured correction rather than a
# design: a path immediately after a colon (``PATH=/usr/bin:/home/jane/.local``,
# ``deploy@host:/srv/www``) was invisible here, and the ``file://`` form is
# matched by its own alternative because two slashes precede the path. Both are
# net repairs, not the fix -- the fix is that the sanitiser no longer carries
# captured text at all.
_PATH_SEGMENT = r"(?:[^\s\"'`<>,;:)\]}/\\]|\\u[0-9a-fA-F]{4})+"
_WINDOWS_SEPARATOR = r"[\\/]{1,2}"
OPERATOR_PATH: re.Pattern[str] = re.compile(
    rf"file://(?:localhost)?/{_PATH_SEGMENT}(?:/{_PATH_SEGMENT})*"
    rf"|(?<![A-Za-z0-9_/.\\])(?:"
    rf"[A-Za-z]:{_WINDOWS_SEPARATOR}{_PATH_SEGMENT}(?:{_WINDOWS_SEPARATOR}{_PATH_SEGMENT})*"
    rf"|/{_PATH_SEGMENT}(?:/{_PATH_SEGMENT})+)"
)

# Absolute paths that name no operator. Both entries are measured against the
# committed corpus rather than imagined, and both are matched against the whole
# path:
#
# * ``/abs/path/…``, the literal documentation placeholder Codex's own base
#   instructions use for "an absolute path" (``e.g. /abs/path/app.py``);
# * ``/root/taskN[/task_M]``, the agent-task namespace Codex's own
#   ``spawn_agent`` description documents. The prefix is *not* exempt:
#   ``/root/<repo>`` is where ``docker run`` and devcontainers keep the
#   operator's checkout.
_CODEX_VOCABULARY_PATH = re.compile(r"\A(?:/abs/path/|/root/task[0-9]+(?:/task_[0-9]+)*\Z)")

# Account and host names, matched by the shape that makes a token an account or
# a host name rather than by the name of the machine running this script. The
# pass this replaced read ``socket.gethostname()`` and ``getpass.getuser()``: of
# 57 plausible host names tried, 36 red-lined the *pristine* corpus with an
# unclearable finding, because a Codex payload is mostly English prose.
_ACCOUNT_AT_HOST = re.compile(r"(?<![A-Za-z0-9_.+@-])[A-Za-z0-9_][A-Za-z0-9._-]{1,31}@[A-Za-z][A-Za-z0-9.-]{1,63}")
# Only with the trailing separator: ``~jane/`` is a home directory, ``~half`` is
# prose and ``~/`` names nobody.
_HOME_REFERENCE = re.compile(r"(?<![A-Za-z0-9_.~-])~[A-Za-z_][A-Za-z0-9._-]{0,31}/")
_IDENTITY_ASSIGNMENT = re.compile(r"\b(?:USER|USERNAME|LOGNAME|HOSTNAME|HOST)\s*=\s*[\"\']?[A-Za-z0-9][A-Za-z0-9._-]*")

OPERATOR_IDENTITY_KIND = "operator_identity"
OPERATOR_PATH_KIND = "operator_path"


def names_an_operator_location(path: str, placeholders: Placeholders = PLACEHOLDERS) -> bool:
    """Whether an absolute path found by ``OPERATOR_PATH`` is the operator's.

    The placeholder namespace is compared by exact value, not by prefix, so
    ``/workspace/<repo>`` -- a real devcontainer checkout -- is still the
    operator's while the corpus's own two placeholders are not.
    """

    return path not in {placeholders.workspace, placeholders.skill_root} and not _CODEX_VOCABULARY_PATH.match(path)


def carries_operator_identity(text: str) -> bool:
    """Whether ``text`` carries an account or host name in one of the three shapes."""

    return any(
        pattern.search(text) is not None for pattern in (_ACCOUNT_AT_HOST, _HOME_REFERENCE, _IDENTITY_ASSIGNMENT)
    )


def _unsanitised_environment_tag_pattern() -> re.Pattern[bytes]:
    """``<cwd>``/``<timezone>``/… carrying anything but the corpus placeholder.

    A rebuilt capture carries no environment context at all -- the text it lived
    in is synthesised whole -- so on a rebuilt fixture this fires only on a
    hand-written or a hand-edited one. Over a raw capture directory, which the
    runbook also scans, it fires by construction. The value is the finding, not
    the tag.
    """

    alternatives = []
    for tag, attribute in ENVIRONMENT_TAGS:
        opening = re.escape(f"<{tag}>".encode())
        placeholder = re.escape(f"{getattr(PLACEHOLDERS, attribute)}</{tag}>".encode())
        alternatives.append(opening + rb"(?!" + placeholder + rb")")
    return re.compile(rb"|".join(alternatives))


def _unsanitised_skill_inventory_pattern() -> re.Pattern[bytes]:
    """The operator's installed skill names and skill-root paths, unreplaced.

    Both forms are matched against the JSON escaping the bodies are stored in
    (``\\n``) as well as a literal newline.
    """

    newline = rb"(?:\\n|\n)"
    inventory = rb"### Available skills" + newline + rb"(?!- " + re.escape(PLACEHOLDERS.skill_name.encode()) + rb")"
    root_entry = newline + rb"- `[^`]{1,80}` *= *`(?!" + re.escape(PLACEHOLDERS.skill_root.encode()) + rb"`)"
    return re.compile(inventory + rb"|" + root_entry)


_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "identifier_key",
        re.compile(
            rb'"(?:'
            + rb"|".join(key.encode() for key in _STRING_VALUED_IDENTIFIER_KEYS)
            + rb')"\s*:\s*"(?!00000000-0000-4000-8000-\d{12}")',
        ),
    ),
    (
        TELEMETRY_KEY_KIND,
        re.compile(rb"(?:client_metadata|access_programs|chatgpt-account-id|x-codex-[a-z-]+)"),
    ),
    ("email", re.compile(rb"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("unsanitised_environment_tag", _unsanitised_environment_tag_pattern()),
    ("unsanitised_skill_inventory", _unsanitised_skill_inventory_pattern()),
)

_OPERATOR_PATH_BYTES = re.compile(OPERATOR_PATH.pattern.encode())


def _names_an_operator_location(match: bytes) -> bool:
    # A chunk boundary can split a UTF-8 sequence; a replacement character only
    # ever makes a path *unlike* the exempt values, so the decode fails closed.
    return names_an_operator_location(match.decode("utf-8", "replace"))


def _sample_kinds(sample: bytes) -> Iterator[str]:
    """The two kinds that are conditioned on a value rather than on a literal."""

    if any(_names_an_operator_location(match.group()) for match in _OPERATOR_PATH_BYTES.finditer(sample)):
        yield OPERATOR_PATH_KIND
    if carries_operator_identity(sample.decode("utf-8", "replace")):
        yield OPERATOR_IDENTITY_KIND


def findings_for_bytes(sample: bytes) -> list[str]:
    """Residual kinds found in one blob of bytes; never the offending value."""

    kinds: set[str] = set()
    for label, pattern in _PATTERNS:
        if pattern.search(sample):
            kinds.add(label)
    for match in _UUID.finditer(sample):
        if not is_placeholder_uuid(match.group().decode()):
            kinds.add("uuid")
            break
    kinds.update(_sample_kinds(sample))
    return sorted(kinds)


def body_findings(payload: Mapping[str, Any]) -> list[str]:
    """Residual kinds in an in-memory body, serialised the way a fixture is stored."""

    return findings_for_bytes(json.dumps(payload, indent=2, sort_keys=True).encode())


def compare_captured_and_sanitised(
    captured: Mapping[str, Any],
    sanitised: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Scan both sides of a rebuild and label the difference.

    Returns the kinds found in the capture, the kinds still found after the
    rebuild, and the difference. The difference is *evidence about the rebuild*:
    the previous version scanned only the rebuilt body, so a capture whose
    operator path had been rewritten -- and whose rewrite had deleted this gate's
    trigger -- reported a clean pass. A caller must never read ``cleared_kinds``
    as a clearance; ``residual_kinds`` being empty is the only green signal here,
    and even that is a net, not a boundary.
    """

    captured_kinds = body_findings(captured)
    residual_kinds = body_findings(sanitised)
    return {
        "captured_kinds": captured_kinds,
        "residual_kinds": residual_kinds,
        "cleared_kinds": sorted(set(captured_kinds) - set(residual_kinds)),
    }


def _chunks(path: Path) -> Iterator[bytes]:
    tail = b""
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            sample = tail + chunk
            yield sample
            tail = sample[-_OVERLAP_BYTES:]


def identifier_findings(path: Path) -> list[str]:
    """Residual kinds found in ``path``; never the offending value."""

    kinds: set[str] = set()
    for sample in _chunks(path):
        kinds.update(findings_for_bytes(sample))
    return sorted(kinds)


def _identifier_pass(root: Path, allow_telemetry_keys: frozenset[str]) -> tuple[list[dict[str, Any]], int]:
    findings: list[dict[str, Any]] = []
    scanned = 0
    for current, _directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(file_names):
            candidate = current_path / name
            if candidate.is_symlink() or candidate.suffix.casefold() not in IDENTIFIER_SCAN_SUFFIXES:
                continue
            relative = str(candidate.relative_to(root))
            try:
                kinds = identifier_findings(candidate)
            except OSError as exc:
                findings.append({"path": relative, "kinds": [f"read_error:{type(exc).__name__}"]})
                continue
            scanned += 1
            if relative in allow_telemetry_keys:
                kinds = [kind for kind in kinds if kind != TELEMETRY_KEY_KIND]
            if kinds:
                findings.append({"path": relative, "kinds": kinds})
    return findings, scanned


def declared_telemetry_key_bodies(root: Path) -> frozenset[str]:
    """Root-relative bodies that *declare* they carry bare Codex telemetry keys.

    Read from ``provenance.json``: a pre-strip fixture exists to feed
    ``strip_source_telemetry``, and ``carries_client_telemetry`` is where it says
    so. ``provenance.json`` exempts itself for the same reason the README is
    prose -- it *names* the fields the rebuild removes. Every value-shaped kind
    (a live UUID, a workspace path, an email, an operator identity, a credential
    shape) still applies to all of them.

    A tree without a ``provenance.json`` -- a raw capture directory, say -- gets
    no exemption at all, which is the answer an operator wants there.
    """

    try:
        payload = json.loads((root / PROVENANCE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    fixtures = payload.get("fixtures") if isinstance(payload, dict) else None
    if not isinstance(fixtures, dict):
        return frozenset()
    declared = {
        str(name)
        for name, entry in fixtures.items()
        if isinstance(entry, dict) and entry.get("carries_client_telemetry")
    }
    return frozenset(declared | {PROVENANCE_NAME})


def scan_fixture_tree(
    root: str | Path,
    *,
    allow_telemetry_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Credential scan over every file plus the residual scan over the bodies.

    The bodies that may carry bare Codex telemetry key names are read from
    ``provenance.json``; ``allow_telemetry_keys`` adds to that, for a tree that
    has no provenance file. Every other kind is rejected everywhere.

    Report shape mirrors ``privacy_scan.scan_tree`` and adds ``bodies_scanned``
    and ``telemetry_key_exemptions``; findings from both passes are merged per
    path.
    """

    root_path = Path(root).resolve()
    exemptions = declared_telemetry_key_bodies(root_path) | allow_telemetry_keys
    credential = scan_tree(root_path)
    identifier, bodies_scanned = _identifier_pass(root_path, exemptions)
    merged: dict[str, set[str]] = {}
    for finding in [*credential["findings"], *identifier]:
        merged.setdefault(str(finding["path"]), set()).update(finding["kinds"])
    findings = [{"path": path, "kinds": sorted(kinds)} for path, kinds in sorted(merged.items())]
    return {
        "schema_version": 1,
        "root": str(root_path),
        "passed": not findings,
        "files_scanned": credential["files_scanned"],
        "bodies_scanned": bodies_scanned,
        "bytes_scanned": credential["bytes_scanned"],
        "telemetry_key_exemptions": sorted(exemptions),
        "findings": findings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Fixture corpus directory to scan")
    parser.add_argument(
        "--captured-body",
        type=Path,
        help="Raw captured body, scanned alongside --sanitised-body so the difference is reported",
    )
    parser.add_argument("--sanitised-body", type=Path, help="Rebuilt body to scan for residual kinds")
    parser.add_argument("--output", type=Path, help="Optional JSON report destination")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--allow-telemetry-keys",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Extra root-relative body that may keep bare Codex telemetry key names. "
            "Bodies declaring carries_client_telemetry in provenance.json are already exempt."
        ),
    )
    return parser


def _compare_from_files(captured: Path, sanitised: Path, *, strict: bool) -> int:
    comparison = compare_captured_and_sanitised(
        json.loads(captured.read_text(encoding="utf-8")),
        json.loads(sanitised.read_text(encoding="utf-8")),
    )
    print(f"captured body {captured}: {', '.join(comparison['captured_kinds']) or 'no residual-scan kinds'}")
    print(f"rebuilt body  {sanitised}: {', '.join(comparison['residual_kinds']) or 'no residual-scan kinds'}")
    if comparison["cleared_kinds"]:
        print(
            "  kinds the capture carried and the rebuild does not: "
            f"{', '.join(comparison['cleared_kinds'])} -- evidence about the rebuild, never a clearance."
        )
    return 2 if strict and comparison["residual_kinds"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.captured_body and args.sanitised_body:
        return _compare_from_files(args.captured_body, args.sanitised_body, strict=args.strict)
    if args.root is None:
        print("Nothing to scan: pass --root, or --captured-body with --sanitised-body.", file=sys.stderr)
        return 2
    result = scan_fixture_tree(args.root, allow_telemetry_keys=frozenset(args.allow_telemetry_keys))
    if args.output:
        atomic_write_json(args.output, result)
    exemptions = result["telemetry_key_exemptions"]
    print(
        f"Fixture privacy scan: {'PASS' if result['passed'] else 'FAIL'}; "
        f"{result['files_scanned']} file(s), {result['bodies_scanned']} body/bodies, "
        f"telemetry-key exemption(s): {', '.join(exemptions) if exemptions else 'none'}"
    )
    for finding in result["findings"]:
        print(f"  {finding['path']}: {', '.join(finding['kinds'])}")
    return 2 if args.strict and not result["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
