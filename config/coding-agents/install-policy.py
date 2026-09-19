#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

START = "<!-- agent-lb:coding-agent-routing:start -->"
END = "<!-- agent-lb:coding-agent-routing:end -->"
MODEL = "fable"
EFFORT_LEVEL = "high"
AGENT_NAMES = (
    "frontend-designer",
    "planner",
    "plan-reviewer",
    "opus-seat",
    "Explore",
    "verifier",
    "cursor-seat",
    "codex-verifier",
)
HOOK_NAMES = ("seat-guard", "subagent-closeout", "routing-pulse")
# The route CLI and its table are lane S3's files; install them only once they
# exist. seat-guard reads the table to infer a dispatch's task class, and the
# launchd job below runs the CLI from its installed path.
OPTIONAL_FILES = (
    (
        Path(".agent-lb/managed/coding-agents/routing-table.json"),
        "routing-table",
        Path("routing-table.json"),
        0o644,
    ),
    (Path(".agent-lb/bin/route"), "route-cli", Path("../../clients/route"), 0o755),
)


def managed_files() -> tuple:
    """(destination, ownership marker path, marker text, template, mode) each."""
    entries = []
    for name in AGENT_NAMES:
        entries.append((Path(f".claude/agents/{name}.md"), name, Path(f"agents/{name}.md"), 0o644))
    for name in HOOK_NAMES:
        entries.append((Path(f".claude/hooks/{name}.py"), name, Path(f"hooks/{name}.py"), 0o755))
    entries.extend(OPTIONAL_FILES)
    return tuple(
        (
            destination,
            Path(f".agent-lb/managed/coding-agents/{key}"),
            f"agent-lb:{key}:v1\n",
            template,
            mode,
        )
        for destination, key, template, mode in entries
    )


MANAGED_AGENTS = managed_files()
# label, plist destination, program arguments (paths resolved against --home)
LAUNCHD_JOB = (
    "com.aneyman.route-doctor",
    Path("Library/LaunchAgents/com.aneyman.route-doctor.plist"),
    (".agent-lb/bin/route", "doctor", "--write"),
    1800,
)
# Hook entries this installer owns: (event, matcher, hook name, timeout, status).
# A matcher of None means the event takes no matcher (it always fires).
MANAGED_HOOKS = (
    ("PreToolUse", "Agent", "seat-guard", 5, "Seat guard"),
    ("SubagentStop", None, "subagent-closeout", 5, "Dispatch closeout"),
    ("UserPromptSubmit", None, "routing-pulse", 3, "Routing pulse"),
)
LEGACY_HEADINGS = (
    "Coding-agent routing",
    "Orchestration — Fable architects, the fleet executes",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--print", action="store_true", dest="preview")
    parser.add_argument("--uninstall", action="store_true")
    return parser.parse_args()


def read_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def validate_markers(text: str, path: Path) -> None:
    starts = text.count(START)
    ends = text.count(END)
    if starts != ends or starts > 1:
        raise ValueError(f"malformed or duplicate managed routing markers in {path}")
    if starts and text.index(START) > text.index(END):
        raise ValueError(f"reversed managed routing markers in {path}")


def h2_span(text: str, headings: tuple[str, ...], path: Path) -> tuple[int, int] | None:
    matches: list[re.Match[str]] = []
    for heading in headings:
        pattern = re.compile(rf"(?m)^##[ \t]+{re.escape(heading)}[ \t]*$")
        matches.extend(pattern.finditer(text))
    if len(matches) > 1:
        raise ValueError(f"multiple legacy routing sections in {path}")
    if not matches:
        return None
    start = matches[0].start()
    next_h2 = re.search(r"(?m)^##[ \t]+", text[matches[0].end() :])
    end = matches[0].end() + (next_h2.start() if next_h2 else len(text[matches[0].end() :]))
    return start, end


def install_adapter(text: str, template: str, path: Path) -> str:
    validate_markers(text, path)
    if START in text:
        start = text.index(START)
        end = text.index(END, start) + len(END)
        updated = text[:start] + template.strip() + text[end:]
    else:
        span = h2_span(text, LEGACY_HEADINGS, path)
        if span:
            updated = text[: span[0]] + template.strip() + "\n\n" + text[span[1] :].lstrip("\n")
        elif text:
            updated = text.rstrip() + "\n\n" + template.strip() + "\n"
        else:
            updated = template.strip() + "\n"
    return updated if updated.endswith("\n") else updated + "\n"


def uninstall_adapter(text: str, path: Path) -> str:
    validate_markers(text, path)
    if START not in text:
        return text
    start = text.index(START)
    end = text.index(END, start) + len(END)
    updated = text[:start].rstrip() + "\n\n" + text[end:].lstrip("\n")
    return updated.rstrip() + "\n" if updated.strip() else ""


def is_owned_hook(command: Any) -> bool:
    return isinstance(command, str) and "ccdex-gpt-only.sh" in command


def hook_entry(name: str, timeout: int, status: str) -> dict[str, Any]:
    return {
        "type": "command",
        "command": f'/usr/bin/python3 "$HOME/.claude/hooks/{name}.py" 2>/dev/null || true',
        "timeout": timeout,
        "statusMessage": status,
    }


def owns(command: Any, name: str) -> bool:
    return isinstance(command, str) and f".claude/hooks/{name}.py" in command


def same_matcher(group: dict[str, Any], matcher: str | None) -> bool:
    current = str(group.get("matcher") or "").strip()
    if matcher is None:
        return current in ("", "*")
    return current == matcher


def reconcile_managed_hooks(hooks: dict[str, Any], uninstall: bool) -> None:
    """Place each owned hook exactly once, in a group with the right matcher.

    An occurrence already sitting in a correctly-matched group is replaced where
    it stands, so a converged settings.json does not churn; occurrences anywhere
    else are removed. Every hook this installer does not own is left untouched.
    """
    for event, matcher, name, timeout, status in MANAGED_HOOKS:
        groups = hooks.get(event) or []
        entry = hook_entry(name, timeout, status)
        placed = False
        for group in groups:
            kept: list[dict[str, Any]] = []
            for hook in group.get("hooks") or []:
                if not owns(hook.get("command"), name):
                    kept.append(hook)
                elif not uninstall and not placed and same_matcher(group, matcher):
                    kept.append(entry)
                    placed = True
            group["hooks"] = kept
        if not uninstall and not placed:
            for group in groups:
                if same_matcher(group, matcher):
                    group["hooks"] = list(group.get("hooks") or []) + [entry]
                    placed = True
                    break
        if not uninstall and not placed:
            group = {"hooks": [entry]}
            if matcher is not None:
                group = {"matcher": matcher, "hooks": [entry]}
            groups = list(groups) + [group]
        groups = [group for group in groups if group.get("hooks")]
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)


def reconcile_settings(settings: dict[str, Any], uninstall: bool) -> dict[str, Any]:
    updated = json.loads(json.dumps(settings))
    hooks = updated.get("hooks", {})
    groups = hooks.get("PreToolUse", [])
    cleaned: list[dict[str, Any]] = []
    for group in groups:
        group_copy = dict(group)
        remaining = [hook for hook in group.get("hooks", []) if not is_owned_hook(hook.get("command"))]
        if remaining:
            group_copy["hooks"] = remaining
            cleaned.append(group_copy)
    if groups:
        if cleaned:
            hooks["PreToolUse"] = cleaned
        else:
            hooks.pop("PreToolUse", None)
    reconcile_managed_hooks(hooks, uninstall)
    if hooks:
        updated["hooks"] = hooks
    else:
        updated.pop("hooks", None)
    if not uninstall:
        updated["model"] = MODEL
        updated["effortLevel"] = EFFORT_LEVEL
    return updated


def plist_text(home: Path) -> str:
    label, _, arguments, interval = LAUNCHD_JOB
    program = [str(home / arguments[0]), *arguments[1:]]
    rendered = "\n".join(f"    <string>{value}</string>" for value in program)
    logs = home / ".agent-lb" / "logs"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"  <key>Label</key>\n  <string>{label}</string>\n"
        f"  <key>ProgramArguments</key>\n  <array>\n{rendered}\n  </array>\n"
        f"  <key>StartInterval</key>\n  <integer>{interval}</integer>\n"
        "  <key>RunAtLoad</key>\n  <true/>\n"
        f"  <key>StandardOutPath</key>\n  <string>{logs / 'route-doctor.log'}</string>\n"
        f"  <key>StandardErrorPath</key>\n  <string>{logs / 'route-doctor.err'}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def write_atomic(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    args = parse_args()
    source = Path(__file__).resolve().parent
    claude_path = args.home / ".claude" / "CLAUDE.md"
    codex_path = args.home / ".codex" / "AGENTS.md"
    template = (source / "claude-adapter.md").read_text()
    originals = {path: read_text(path) for path in (claude_path, codex_path)}
    settings_path = args.home / ".claude" / "settings.json"
    settings_text = read_text(settings_path)
    try:
        settings = json.loads(settings_text) if settings_text else {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: invalid JSON in {settings_path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise SystemExit(f"error: expected a JSON object in {settings_path}")

    try:
        desired_docs = {
            claude_path: (
                uninstall_adapter(originals[claude_path], claude_path)
                if args.uninstall
                else install_adapter(originals[claude_path], template, claude_path)
            ),
            # The codex-host adapter is retired: converge always removes its managed block.
            codex_path: uninstall_adapter(originals[codex_path], codex_path),
        }
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    desired_settings = reconcile_settings(settings, args.uninstall)
    desired_settings_text = json.dumps(desired_settings, indent=2) + "\n"
    changes: dict[Path, str | None] = {
        path: desired for path, desired in desired_docs.items() if desired != originals[path]
    }
    if desired_settings_text != settings_text:
        changes[settings_path] = desired_settings_text
    modes: dict[Path, int] = {}
    preserved_agents: list[tuple[str, Path]] = []
    adopted: list[tuple[Path, Path]] = []
    backup_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    for relative_path, owner_relative_path, owner_marker, template_relative_path, mode in MANAGED_AGENTS:
        template_path = source / template_relative_path
        if not template_path.exists():
            continue
        agent_path = args.home / relative_path
        owner_path = args.home / owner_relative_path
        agent_template = template_path.read_text()
        agent_text = read_text(agent_path)
        modes[agent_path] = mode
        agent_owned = read_text(owner_path) == owner_marker
        if args.uninstall:
            if agent_owned:
                changes[owner_path] = None
                if agent_text == agent_template:
                    changes[agent_path] = None
                elif agent_path.exists():
                    preserved_agents.append(("customized", agent_path))
            elif agent_path.exists():
                preserved_agents.append(("unmanaged", agent_path))
        else:
            # Adoption: a file that predates this installer (Explore.md,
            # verifier.md, seat-guard.py, routing-pulse.py all exist today) is
            # backed up once, beside itself, before it is first overwritten.
            if agent_text and not agent_owned and agent_text != agent_template:
                if not any(agent_path.parent.glob(f"{agent_path.name}.pre-router-*")):
                    backup_path = agent_path.with_name(f"{agent_path.name}.pre-router-{backup_stamp}")
                    changes[backup_path] = agent_text
                    modes[backup_path] = mode
                    adopted.append((agent_path, backup_path))
            if agent_text != agent_template:
                changes[agent_path] = agent_template
            if not agent_owned:
                changes[owner_path] = owner_marker

    # The launchd job that keeps ~/.claude/routing-state.json fresh. The plist
    # is written, never loaded: loading it is a deploy step, not an install one.
    plist_path = args.home / LAUNCHD_JOB[1]
    desired_plist = plist_text(args.home)
    if args.uninstall:
        if plist_path.exists():
            changes[plist_path] = None
    elif read_text(plist_path) != desired_plist:
        changes[plist_path] = desired_plist

    action = (
        "remove managed routing configuration from" if args.uninstall else "converge managed routing configuration in"
    )
    if args.preview:
        for original, backup_path in adopted:
            print(f"would back up {original} to {backup_path}")
        for path in changes:
            print(f"would {action} {path}")
        for reason, path in preserved_agents:
            print(f"would preserve {reason} {path}")
        if not changes:
            print("managed routing configuration already converged")
        return 0
    if not changes:
        for reason, path in preserved_agents:
            print(f"preserved {reason} {path}")
        print("managed routing configuration already converged")
        return 0

    checkpoint_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    checkpoint = args.home / ".agent-lb" / "config-checkpoints" / "coding-agents" / checkpoint_name
    checkpoint.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, str] = {}
    for path in changes:
        if path.exists():
            relative = path.relative_to(args.home)
            destination = checkpoint / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            manifest[str(relative)] = "copied"
        else:
            manifest[str(path.relative_to(args.home))] = "absent"
    (checkpoint / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"checkpoint {checkpoint}")
    for path, content in changes.items():
        if content is None:
            path.unlink()
            print(f"removed {path}")
        else:
            write_atomic(path, content, modes.get(path))
            print(f"updated {path}")
    for original, backup_path in adopted:
        print(f"backed up {original} to {backup_path}")
    for reason, path in preserved_agents:
        print(f"preserved {reason} {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
