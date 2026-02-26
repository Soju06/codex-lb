#!/usr/bin/env python3
"""PostToolUse hook — track Skill tool invocations in session state.

Matcher: Skill

Records skill usage in skills-used-{session_id}.json so that
skill_guard.py can check sessionSkillUsed and allow edits.
"""

import json
import sys
from pathlib import Path

try:
    from _analytics import emit_event
except ImportError:

    def emit_event(*_a: object, **_k: object) -> None:
        pass


def record_skill_used(session_id: str, skill_name: str) -> None:
    """Record skill invocation in session state for skill_guard lookups."""
    hook_dir = Path(__file__).resolve().parent
    state_dir = hook_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"skills-used-{session_id}.json"

    state: dict[str, list[str]] = {"suggestedSkills": [], "usedSkills": []}
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (json.JSONDecodeError, PermissionError):
            pass

    used = state.get("usedSkills", [])
    if skill_name not in used:
        used.append(skill_name)
    state["usedSkills"] = used

    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


def main() -> None:
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    session_id = input_data.get("session_id", "unknown")
    tool_input = input_data.get("tool_input", {})
    skill_name = tool_input.get("skill", "")

    if not skill_name:
        sys.exit(0)

    record_skill_used(session_id, skill_name)

    emit_event(
        session_id,
        "hook.invoked",
        {
            "hook": "skill_tracker",
            "trigger": "PostToolUse",
            "outcome": "tracked",
            "skill": skill_name,
            "exit_code": 0,
        },
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
