## ADDED Requirements

### Requirement: Clear and responsive installer presentation

The Install tab SHALL visually distinguish platform selection, the direct terminal command, and file export actions. It SHALL display the selected shell and installer filename and group prerequisite, replacement, restart, remote-environment, and credential-export guidance separately from executable previews. Existing Overview content and installer authentication and export behavior MUST remain unchanged.

The presentation MUST support keyboard-operable platform selection with an accessible group name, visible focus, and a selected-state indicator that does not rely only on color. Commands and controls MUST remain readable and operable in light and dark themes at desktop and mobile widths without page-level horizontal overflow.

#### Scenario: Choose a platform and export

- **WHEN** a user chooses macOS, Linux, or Windows in Install
- **THEN** the selected platform, shell, command, filename, and exported content match that choice
- **AND** the direct copy action is distinguishable from script copying and downloading
- **AND** previews remain masked while exports contain the current key

#### Scenario: Use keyboard navigation

- **WHEN** a user navigates to the platform group by keyboard and changes its selection
- **THEN** the selected radio exposes its checked state and visible focus
- **AND** the associated command and export controls update

#### Scenario: Read setup on a narrow screen

- **WHEN** the Install tab is viewed at a 390-pixel viewport in either theme
- **THEN** its cards stack, all setup actions remain usable, and long commands or expanded previews do not cause page-level horizontal overflow
- **AND** prerequisite and security guidance remains visible without expanding the preview
