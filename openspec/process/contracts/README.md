# Process Contracts

One YAML file per task type. Every file in this directory MUST validate
against [`schema.json`](schema.json). Run the validator locally:

```bash
uv run python openspec/process/scripts/validate_contracts.py
```

## Authoring rules

1. **Filename = contract name.** `feature.yaml` has `name: feature`.
2. **One trigger per contract.** The `trigger` field is the slash command
   the user types to load it.
3. **Phases are ordered.** Each phase declares `irreversible: true|false`.
   When `true`, you MUST also declare `confirmation_phrase` — the exact
   string the user types to advance.
4. **`stop_signals` are an enum.** Pick from the seven values defined in
   `schema.json`. Do not invent new ones; extend the schema first.
5. **`interruption_commands` are also an enum.** `stop`, `rollback`,
   `explain`, `skip`.
6. **`history_target` is required for any contract that produces
   history.** Use `openspec-change-notes` for code work and
   `release-log` for releases.

## Adding a new contract

1. Copy the closest existing contract.
2. Edit phases. Validate: `uv run python openspec/process/scripts/validate_contracts.py`.
3. Add a row to `../process-map.md` under "Triggers".
4. Add a row to `../process-map.md` under "Approval phrases" if you have
   irreversible phases.
5. Open a PR. The `process-check` workflow validates everything for you.
