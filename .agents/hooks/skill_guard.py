#!/usr/bin/env python3
"""PreToolUse hook — enforce guardrail skills before Edit/Write operations."""

import json
import os
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

try:
    from _analytics import emit_event
except ImportError:

    def emit_event(*_a: object, **_k: object) -> None:
        pass


def load_skill_rules() -> dict:
    """Load skill-rules.json relative to this hook's location."""
    hook_dir = Path(__file__).resolve().parent
    rules_path = hook_dir.parent / "skills" / "skill-rules.json"
    with open(rules_path) as f:
        return json.load(f)


def load_session_state(session_id: str) -> dict:
    """Load session state for tracking which skills have been used."""
    hook_dir = Path(__file__).resolve().parent
    state_path = hook_dir / "state" / f"skills-used-{session_id}.json"
    if state_path.exists():
        try:
            with open(state_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    return {"suggestedSkills": [], "usedSkills": []}


def match_path_patterns(file_path: str, patterns: list[str]) -> bool:
    """Check if file_path matches any glob pattern."""
    return any(fnmatch(file_path, pat) for pat in patterns)


def match_content_patterns(file_path: str, patterns: list[str]) -> bool:
    """Check if file content matches any regex pattern."""
    try:
        content = Path(file_path).read_text(errors="ignore")
        return any(re.search(pat, content) for pat in patterns)
    except (FileNotFoundError, PermissionError):
        return False


def check_pass_state(pass_state_file: str) -> bool:
    """Check if a pass state file exists and has result=PASS."""
    hook_dir = Path(__file__).resolve().parent
    state_path = hook_dir / "state" / pass_state_file
    if not state_path.exists():
        return False
    try:
        data = json.loads(state_path.read_text())
        return data.get("result") == "PASS"
    except (json.JSONDecodeError, PermissionError):
        return False


def check_file_markers(file_path: str, markers: list[str]) -> bool:
    """Check if file contains any skip markers."""
    try:
        content = Path(file_path).read_text(errors="ignore")
        return any(marker in content for marker in markers)
    except (FileNotFoundError, PermissionError):
        return False


def main() -> None:
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)  # fail-open

    session_id = input_data.get("session_id", "unknown")
    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    try:
        rules = load_skill_rules()
    except (FileNotFoundError, json.JSONDecodeError):
        sys.exit(0)  # fail-open

    skills = rules.get("skills", {})

    session_state = load_session_state(session_id)

    # --- Phase 1: Hard block guardrails ---
    guardrails = {
        name: rule
        for name, rule in skills.items()
        if rule.get("type") == "guardrail" and rule.get("enforcement") == "block"
    }

    for name, rule in guardrails.items():
        file_triggers = rule.get("fileTriggers")
        if not file_triggers:
            continue

        path_patterns = file_triggers.get("pathPatterns", [])
        if not match_path_patterns(file_path, path_patterns):
            continue

        path_exclusions = file_triggers.get("pathExclusions", [])
        if path_exclusions and match_path_patterns(file_path, path_exclusions):
            continue

        content_patterns = file_triggers.get("contentPatterns", [])
        if content_patterns and not match_content_patterns(file_path, content_patterns):
            continue

        # --- Skip conditions ---
        skip = rule.get("skipConditions", {})

        pass_state_file = skip.get("passStateFile")
        if pass_state_file and check_pass_state(pass_state_file):
            continue

        if skip.get("sessionSkillUsed") and name in session_state.get("usedSkills", []):
            continue

        file_markers = skip.get("fileMarkers", [])
        if file_markers and check_file_markers(file_path, file_markers):
            continue

        env_override = skip.get("envOverride")
        if env_override and os.environ.get(env_override):
            continue

        # All checks passed — block
        emit_event(
            session_id,
            "hook.invoked",
            {
                "hook": "skill_guard",
                "trigger": "PreToolUse",
                "outcome": "blocked",
                "matched_count": 1,
                "exit_code": 2,
            },
        )
        block_message = rule.get(
            "blockMessage",
            f"BLOCKED: Skill '{name}' must be invoked before editing this file.\nUse Skill tool: '{name}'",
        )
        print(block_message, file=sys.stderr)
        sys.exit(2)

    # --- Phase 2: Remind enforcement (block until skill invoked in session) ---
    remind_rules = {
        name: rule for name, rule in skills.items() if rule.get("enforcement") == "remind" and rule.get("fileTriggers")
    }

    for name, rule in remind_rules.items():
        file_triggers = rule.get("fileTriggers", {})

        path_patterns = file_triggers.get("pathPatterns", [])
        if not match_path_patterns(file_path, path_patterns):
            continue

        path_exclusions = file_triggers.get("pathExclusions", [])
        if path_exclusions and match_path_patterns(file_path, path_exclusions):
            continue

        content_patterns = file_triggers.get("contentPatterns", [])
        if content_patterns and not match_content_patterns(file_path, content_patterns):
            continue

        # --- Skip conditions ---
        skip = rule.get("skipConditions", {})

        if skip.get("sessionSkillUsed") and name in session_state.get("usedSkills", []):
            continue

        env_override = skip.get("envOverride")
        if env_override and os.environ.get(env_override):
            continue

        # Block with convention reminder
        emit_event(
            session_id,
            "hook.invoked",
            {
                "hook": "skill_guard",
                "trigger": "PreToolUse",
                "outcome": "remind_blocked",
                "matched_count": 1,
                "exit_code": 2,
            },
        )
        block_message = rule.get(
            "blockMessage",
            f"BLOCKED: Run /{name} first.\n"
            f"You must invoke this skill before editing this file.\n\n"
            f"→ Skill tool: '{name}'",
        )
        print(block_message, file=sys.stderr)
        sys.exit(2)

    emit_event(
        session_id,
        "hook.invoked",
        {
            "hook": "skill_guard",
            "trigger": "PreToolUse",
            "outcome": "passed",
            "matched_count": 0,
            "exit_code": 0,
        },
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
