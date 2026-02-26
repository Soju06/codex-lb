#!/usr/bin/env python3
"""UserPromptSubmit hook — suggest relevant skills based on prompt keywords/intent."""

import json
import re
import sys
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


def match_prompt_triggers(prompt: str, triggers: dict) -> bool:
    """Check if prompt matches keyword or intent pattern triggers."""
    prompt_lower = prompt.lower()

    for kw in triggers.get("keywords", []):
        if kw.lower() in prompt_lower:
            return True

    for pattern in triggers.get("intentPatterns", []):
        if re.search(pattern, prompt, re.IGNORECASE):
            return True

    return False


def main() -> None:
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        sys.exit(0)

    prompt = input_data.get("prompt", "")
    if not prompt.strip():
        sys.exit(0)

    try:
        rules = load_skill_rules()
    except (FileNotFoundError, json.JSONDecodeError):
        sys.exit(0)

    skills = rules.get("skills", {})

    # Match each skill's promptTriggers against the user prompt
    matched: list[dict] = []
    for name, rule in skills.items():
        triggers = rule.get("promptTriggers")
        if not triggers:
            continue
        if match_prompt_triggers(prompt, triggers):
            matched.append(
                {
                    "name": name,
                    "priority": rule.get("priority", "medium"),
                    "description": rule.get("description", ""),
                }
            )

    if not matched:
        emit_event(
            input_data.get("session_id", "unknown"),
            "hook.invoked",
            {
                "hook": "skill_activation",
                "trigger": "UserPromptSubmit",
                "outcome": "no_match",
                "matched_count": 0,
                "exit_code": 0,
            },
        )
        sys.exit(0)

    # Group by priority
    priority_order = ["critical", "high", "medium", "low"]
    by_priority: dict[str, list[dict]] = {p: [] for p in priority_order}
    for m in matched:
        by_priority.setdefault(m["priority"], []).append(m)

    # Build output banner
    lines: list[str] = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "SKILL ACTIVATION CHECK",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    labels = {
        "critical": "CRITICAL SKILLS (REQUIRED):",
        "high": "RECOMMENDED SKILLS:",
        "medium": "SUGGESTED SKILLS:",
        "low": "OPTIONAL SKILLS:",
    }

    for priority in priority_order:
        group = by_priority.get(priority, [])
        if not group:
            continue
        lines.append(f"  {labels[priority]}")
        for s in group:
            lines.append(f"    -> {s['name']}")
        lines.append("")

    lines.append("  ACTION: Use Skill tool to invoke matched skills")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    emit_event(
        input_data.get("session_id", "unknown"),
        "hook.invoked",
        {
            "hook": "skill_activation",
            "trigger": "UserPromptSubmit",
            "outcome": "matched",
            "matched_count": len(matched),
            "exit_code": 0,
        },
    )
    print("\n".join(lines))
    sys.exit(0)


if __name__ == "__main__":
    main()
