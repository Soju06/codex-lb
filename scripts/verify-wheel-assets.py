from __future__ import annotations

import zipfile
from pathlib import Path

wheels = sorted(Path("dist").glob("*.whl"))
if not wheels:
    raise SystemExit("no wheel found in dist/")
if len(wheels) != 1:
    wheel_list = ", ".join(str(wheel) for wheel in wheels)
    raise SystemExit(f"expected exactly one wheel in dist/, found {len(wheels)}: {wheel_list}")

wheel = wheels[0]
with zipfile.ZipFile(wheel) as zf:
    names = set(zf.namelist())
    has_index = "app/static/index.html" in names
    has_js = any(name.startswith("app/static/assets/") and name.endswith(".js") for name in names)
    has_css = any(name.startswith("app/static/assets/") and name.endswith(".css") for name in names)

if not (has_index and has_js and has_css):
    raise SystemExit(f"frontend assets missing in wheel: index={has_index} js={has_js} css={has_css}")

print(f"frontend assets verified in wheel: {wheel}")
