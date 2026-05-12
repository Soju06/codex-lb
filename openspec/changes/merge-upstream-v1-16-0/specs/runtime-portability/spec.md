## ADDED Requirements

### Requirement: Runtime portability fixes do not break fork installs

The upstream runtime portability fixes, including portable oversized response dump paths, MUST be adopted in a way that works for container, local `uv`, and packaged fork installs.

#### Scenario: Oversized response dump path is writable in local installs

- **WHEN** the runtime writes an oversized response debug dump outside a container
- **THEN** it uses the configured data/home directory instead of a hard-coded container-only absolute path
- **AND** the path is derived from runtime settings rather than the developer machine layout

#### Scenario: Fork CLI entry points remain valid

- **WHEN** the package is installed after the merge
- **THEN** fork CLI entry points remain importable and executable
- **AND** tests cover the fork entry-point names

### Requirement: Fork install version matches upstream release

After the upstream merge, installed fork metadata MUST report version `1.16.0` while retaining fork-specific package and CLI names.

#### Scenario: Installed package reports merged version

- **WHEN** the merged fork package is built or installed
- **THEN** package metadata reports version `1.16.0`
- **AND** CLI entry points remain the fork-specific names
