from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import main_checkout, worktree_root  # noqa: E402

SCHEMA = worktree_root() / "frontend/src/features/accounts/schemas.ts"
NODE_MODULES = main_checkout() / "frontend/node_modules"


def main() -> None:
    source = SCHEMA.read_text()
    assert '.catch("openai")' not in source
    assert ".catch('openai')" not in source
    script = r"""
const { createRequire } = require("module");
const requireZod = createRequire(require("path").join(process.env.AGENT_LB_NODE_MODULES, "zod/package.json"));
const { z } = requireZod("zod");
const source = process.env.AGENT_LB_SCHEMA_SOURCE;
const enumMatch = source.match(/export const AccountProviderSchema = z\.enum\(\s*\[([\s\S]*?)\]\s*\)/);
if (!enumMatch) {
  throw new Error("AccountProviderSchema not found");
}
const values = [...enumMatch[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
const AccountProviderSchema = z.enum(values);
const kept = ["openai", "anthropic", "glm", "kimi", "openrouter"];
const parsed = Object.fromEntries(kept.map((value) => [value, AccountProviderSchema.parse(value)]));
let unknownThrew = false;
try {
  AccountProviderSchema.parse("mystery");
} catch {
  unknownThrew = true;
}
process.stdout.write(JSON.stringify({ parsed, unknownThrew, values }));
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "AGENT_LB_SCHEMA_SOURCE": source,
            "AGENT_LB_NODE_MODULES": str(NODE_MODULES),
        },
        cwd=str(NODE_MODULES.parent),
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr or completed.stdout or "node parse failed")
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    for value in ["openai", "anthropic", "glm", "kimi", "openrouter"]:
        assert payload["parsed"][value] == value, payload
    assert payload["unknownThrew"] is True


if __name__ == "__main__":
    main()
