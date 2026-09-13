## ADDED Requirements

### Requirement: Dashboard route transitions preserve intentional scroll behavior

The dashboard SPA MUST reset the window to the top when a client-side `PUSH` or `REPLACE` navigation changes the final destination pathname and the destination has no hash. A compatibility route that immediately replaces itself with a hashed destination MUST be treated as part of that hash-target navigation rather than as an independent destination. The same rule MUST apply to desktop and mobile top-level navigation. The SPA MUST NOT perform that reset for browser-history `POP` navigation, same-path query changes, or destinations with a hash.

#### Scenario: Desktop top-level navigation opens the destination at the top

- **GIVEN** a desktop user has scrolled a dashboard page below its heading
- **WHEN** the user activates a top-level link to a different pathname without a hash
- **THEN** the destination opens with `window.scrollY` equal to `0`
- **AND** the destination heading is visible in the viewport

#### Scenario: Mobile top-level navigation opens the destination at the top

- **GIVEN** a mobile user has scrolled a dashboard page below its heading
- **WHEN** the user opens the header menu and activates a top-level link to a different pathname without a hash
- **THEN** the destination opens with `window.scrollY` equal to `0`
- **AND** the destination heading is visible in the viewport

#### Scenario: Browser history keeps its restoration position

- **GIVEN** the browser has a stored scroll position for an earlier pathname
- **WHEN** the user returns through back or forward history navigation
- **THEN** the route shell does not reset the window scroll position

#### Scenario: Query-only navigation keeps the current position

- **GIVEN** the user is viewing a dashboard pathname at a nonzero scroll position
- **WHEN** an in-app filter or view change updates only that pathname's query string
- **THEN** the route shell does not reset the window scroll position

#### Scenario: Settings and Firewall hashes retain target scrolling

- **WHEN** navigation targets `/settings?advanced=1#firewall` directly or through the `/firewall` or `/firewall/` compatibility redirect
- **THEN** the route shell does not reset the window to the top
- **AND** the existing Settings hash behavior brings the Firewall target into view
