## ADDED Requirements

### Requirement: App header brand links to dashboard

The app header brand area SHALL render a `<Link to="/dashboard">` wrapping the
logo and "Codex LB" text so that clicking the brand navigates back to the
dashboard home page. The link SHALL preserve the existing visual layout (logo
size, gradient background, text styling) and SHALL include keyboard
focus-visible ring styling matching the project's existing interactive-element
conventions.

#### Scenario: Brand click navigates to dashboard

- **WHEN** an operator clicks the header brand area (logo or "Codex LB" text)
- **THEN** the SPA navigates to `/dashboard`

#### Scenario: Brand link is keyboard-accessible

- **WHEN** an operator tabs to the header brand
- **THEN** the brand area receives a visible focus ring
- **AND** pressing Enter navigates to `/dashboard`

#### Scenario: Brand link preserves visual appearance

- **WHEN** the header renders
- **THEN** the logo and "Codex LB" text appear visually identical to the prior
  non-interactive `<div>` layout
