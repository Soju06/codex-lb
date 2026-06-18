## 1. Implementation

- [x] 1.1 Replace pre-migration database file copying with SQLite online backup.
- [x] 1.2 Serialize account mutation methods with the shared SQLite writer section.

## 2. Tests

- [x] 2.1 Cover WAL-backed pre-migration backup snapshots.
- [x] 2.2 Cover account status and token writes entering the SQLite writer section.

## 3. Validation

- [x] 3.1 Run focused unit tests.
- [x] 3.2 Run lint/type checks relevant to the patch.
- [ ] 3.3 Validate the OpenSpec change and specs.
