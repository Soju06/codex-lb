# Context: settings-reference-page

## Decisions

- **Generated page is checked in, not built on the fly.** The docs workflow
  installs only the `docs` dependency group; importing `Settings` at docs
  build time would drag the whole application dependency tree into the docs
  build. Checking the page in keeps `mkdocs build --strict` hermetic, and the
  regenerate-and-diff unit test (which runs with the full app environment)
  is the freshness guarantee.
- **Symbolic defaults for environment-derived values.** `data_dir`,
  `database_url`, `encryption_key_file`, `conversation_archive_dir`,
  `oauth_callback_host`, `upstream_websocket_trust_env`, and
  `http_responses_session_bridge_instance_id` compute their defaults from the
  runtime environment (home directory, container detection, hostname,
  outbound proxy env). Rendering the live values would make the page differ
  per machine and break the byte-identical drift test in CI, so these render
  as symbolic descriptions (e.g. `<data_dir>/store.db`).
- **No invented prose.** Most fields carry no `Field(description=...)`; the
  source-comment convention in `settings.py` is left as-is. The table of
  name/type/default is the deliverable; a Description column appears only for
  fields that declare one.
- **Ratchet at 115.** The surface is 114 fields today. The ratchet only goes
  down when fields are removed; raising it requires a simplicity-budget
  discussion (PRINCIPLES.md P2, CONTRIBUTING.md simplicity gates, issue
  #1340).
- **Prefix grouping is a heuristic, not a taxonomy.** Longest-matching
  field-name prefix wins, a small exact-name override map handles outliers
  (`data_dir`, `trace`, `workers_per_instance`), and anything unmatched lands
  in "Other". Renaming a group is a one-line generator edit plus a
  regeneration.

## Example

Adding a new setting without regenerating the page fails
`tests/unit/test_settings_reference.py::test_generated_settings_reference_matches_code`;
the fix is `uv run python scripts/generate_settings_reference.py` and
committing the diff. Adding a 116th setting additionally fails the ratchet
until the budget discussion lands.
