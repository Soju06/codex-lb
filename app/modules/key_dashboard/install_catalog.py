"""Standalone catalog setup programs embedded in exported client installers."""

UNIX_CATALOG_SETUP = r"""
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def toml_string(value):
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


codex_dir = Path(sys.argv[1]).resolve()
backup_dir = Path(sys.argv[2])
setup = json.loads((backup_dir / "setup.json").read_text(encoding="utf-8"))
auth = json.loads((backup_dir / "auth.new").read_text(encoding="utf-8"))
request = Request(
    setup["catalog_url"],
    headers={"Authorization": "Bearer " + auth["OPENAI_API_KEY"], "Accept": "application/json"},
)
try:
    with build_opener(NoRedirect).open(request, timeout=30) as response:
        catalog = json.load(response)
except HTTPError as exc:
    sys.exit("Catalog download failed (HTTP %s); check the key and endpoint." % exc.code)
except (URLError, OSError, ValueError):
    sys.exit("Catalog download failed; check connectivity and the server's JSON catalog.")

if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
    sys.exit("Invalid native model catalog; client files were not replaced.")
models = []
slugs = set()
for entry in catalog["models"]:
    if (not isinstance(entry, dict) or not isinstance(entry.get("slug"), str)
            or not entry["slug"] or entry["slug"] in slugs
            or entry.get("visibility") not in ("list", "hide")
            or not isinstance(entry.get("supported_in_api"), bool)
            or not isinstance(entry.get("prefer_websockets"), bool)):
        sys.exit("Invalid native model entry; client files were not replaced.")
    slugs.add(entry["slug"])
    if entry["visibility"] == "list" and entry["supported_in_api"]:
        models.append(entry)
if not models:
    sys.exit("No usable Codex models for this key; ask the administrator to enable a streaming Responses model.")
model = setup["model"] or models[0]["slug"]
if model not in {entry["slug"] for entry in models}:
    sys.exit("The configured model is unavailable; export a new installer after checking the key's model access.")

catalog_path = codex_dir / "codex-lb-models.json"
(backup_dir / "catalog.new").write_text(
    json.dumps({"models": models}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
config_path = backup_dir / "config.new"
config = (
    "model = " + toml_string(model) + "\n"
    + "model_catalog_json = " + toml_string(str(catalog_path)) + "\n"
    + config_path.read_text(encoding="utf-8")
    + "supports_websockets = " + str(all(entry["prefer_websockets"] for entry in models)).lower() + "\n"
)
config_path.write_text(config, encoding="utf-8")
(backup_dir / "setup.json").unlink()
"""

WINDOWS_CATALOG_SETUP = r"""
$setup = $setupJson | ConvertFrom-Json
$credential = ($auth | ConvertFrom-Json).OPENAI_API_KEY
try {
    $catalogResponse = Invoke-WebRequest -UseBasicParsing -Uri $setup.catalog_url `
        -Headers @{ Authorization = ('Bearer ' + $credential); Accept = 'application/json' } `
        -MaximumRedirection 0 -TimeoutSec 30
    if ([int]$catalogResponse.StatusCode -ne 200) { throw 'Unexpected catalog status' }
    $catalog = $catalogResponse.Content | ConvertFrom-Json
} catch {
    throw 'Catalog download failed; check the key, endpoint, and server JSON catalog. Client files were not replaced.'
}
if ($null -eq $catalog -or $catalog.models -isnot [Array]) {
    throw 'Invalid native model catalog; client files were not replaced.'
}
$models = @()
$slugs = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($entry in $catalog.models) {
    if ($entry.slug -isnot [string] -or [string]::IsNullOrEmpty($entry.slug) -or -not $slugs.Add($entry.slug) `
        -or $entry.visibility -cnotin @('list', 'hide') -or $entry.supported_in_api -isnot [bool] `
        -or $entry.prefer_websockets -isnot [bool]) {
        throw 'Invalid native model entry; client files were not replaced.'
    }
    if ($entry.visibility -ceq 'list' -and $entry.supported_in_api) { $models += $entry }
}
if ($models.Count -eq 0) {
    throw 'No usable Codex models for this key; ask the administrator to enable a streaming Responses model.'
}
$model = if ($setup.model) { $setup.model } else { $models[0].slug }
if ($model -cnotin @($models | ForEach-Object { $_.slug })) {
    throw "The configured model is unavailable; export a new installer after checking the key's model access."
}
$catalogPath = Join-Path $codexDir 'codex-lb-models.json'
$catalogJson = ConvertTo-Json -InputObject @{ models = @($models) } -Depth 100
$websockets = (@($models | Where-Object { -not $_.prefer_websockets }).Count -eq 0).ToString().ToLowerInvariant()
# ConvertTo-Json safely quotes TOML basic strings, including Windows path separators.
$modelJson = ConvertTo-Json -InputObject $model -Compress
$pathJson = ConvertTo-Json -InputObject $catalogPath -Compress
$config = "model = $modelJson`nmodel_catalog_json = $pathJson`n" + $config + "`nsupports_websockets = $websockets`n"
"""
