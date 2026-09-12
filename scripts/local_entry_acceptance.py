"""Compatibility entrypoint for the stable entrance's configured validator."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.local_verification import DEFAULT_MODEL, Report, probe  # noqa: E402


async def main() -> None:
    plan = os.environ.get("LOCAL_ENTRY_DATABASE_PLAN")
    await probe(Report(None, timeout=50), os.environ["CANDIDATE_URL"], DEFAULT_MODEL, plan=Path(plan) if plan else None)


if __name__ == "__main__":
    asyncio.run(main())
