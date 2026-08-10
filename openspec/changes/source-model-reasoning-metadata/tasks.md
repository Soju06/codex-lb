## 1. Catalog metadata

- [x] 1.1 Derive source-model reasoning levels, default level, and summary
      support from `raw_metadata_json`.
- [x] 1.2 Restrict the declared default to one of the advertised efforts.

## 2. Effort delivery

- [x] 2.1 Skip the `minimal` normalization for models absent from a populated
      registry snapshot, keeping the conservative rewrite when no snapshot is
      available.

## 3. Verification

- [x] 3.1 Unit coverage for slug lists, object lists, malformed entries, an
      out-of-range default, and the no-metadata default.
- [x] 3.2 Manual end-to-end check that `/backend-api/codex/models` advertises the
      declared efforts and that forwarding behavior is unchanged.
