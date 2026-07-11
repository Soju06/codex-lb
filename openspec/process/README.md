# Process Layer

This directory holds the operator-facing process machinery that ties
together the user, Claude Code, and CI.

```
process/
├── README.md                 ← you are here
├── process-map.md            ← cheat sheet (mermaid + prose)
├── release-log.md            ← release history (append-only)
├── contracts/
│   ├── README.md             ← contract authoring rules
│   ├── schema.json           ← JSON Schema for contracts
│   ├── feature.yaml
│   ├── bugfix.yaml
│   ├── release-beta.yaml
│   ├── release-stable.yaml
│   └── sync-upstream.yaml
└── scripts/
    └── validate_contracts.py
```

The contracts are the source of truth. The cheat sheet mirrors them.
The CLI and the GitHub Action both read the same files.
