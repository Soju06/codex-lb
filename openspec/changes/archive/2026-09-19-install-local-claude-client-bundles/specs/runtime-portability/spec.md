## ADDED Requirements

### Requirement: Installed Claude clients are independent of the source checkout

The Claude client installer MUST copy its public clients, required sibling
helpers, canonical policy, and uninstall entrypoint into a versioned local
runtime bundle before activating it. Installed commands and policy MUST NOT
require access to the source checkout. Reinstalling identical content MUST reuse
the same version. Activation MUST replace the current-version link atomically
and MUST retain prior versions.

#### Scenario: Source checkout becomes unavailable

- **WHEN** the source checkout is moved after installation
- **THEN** installed `cc`, `fable`, `opus`, and `claude-lb-launch` still resolve and run
- **AND** sibling runtime doctors and canonical policy remain available locally
- **AND** the bundled installer can uninstall without the original checkout

#### Scenario: Preview makes no changes

- **WHEN** the installer runs with `--print`
- **THEN** it reports the local bundle and managed link destinations
- **AND** it does not create a bundle, links, backups, or user configuration

#### Scenario: Existing unmanaged commands are preserved

- **WHEN** installation replaces an unmanaged file or symbolic link
- **THEN** the installer preserves it at a `.pre-agent-lb` backup
- **AND** an existing backup conflict aborts before changing managed targets

#### Scenario: Uninstall preserves replacements and rollback versions

- **WHEN** the installer runs with `--uninstall`
- **THEN** it removes only recognized managed client and policy links
- **AND** preserves user replacements, backups, and installed bundle versions

### Requirement: Claude startup checks its process environment

The Claude launcher MUST check whether its working directory is readable before
starting Claude. An unreadable or deleted working directory MUST produce an
actionable error without silently changing the selected project. On systems
with process file-descriptor limits, the launcher MUST raise a low soft limit
within the existing hard limit and a bounded launcher target before spawning
children. It MUST NOT change system-wide limits or require administrator access.

#### Scenario: Working directory is unavailable

- **WHEN** Claude startup cannot read its current working directory
- **THEN** the launcher exits with a working-directory diagnostic
- **AND** explains how to select an accessible project directory
- **AND** does not start Claude in a different project

#### Scenario: Inherited file-descriptor limit is low

- **WHEN** the inherited soft file-descriptor limit is below the launcher target
- **THEN** the launcher attempts to increase its own soft limit before spawning children
- **AND** does not exceed its inherited hard limit or lower an existing higher limit
- **AND** an unsupported or rejected limit adjustment does not prevent startup
