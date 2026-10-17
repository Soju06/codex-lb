## ADDED Requirements

### Requirement: The company sign-in and recovery page

`docs/sso.md` SHALL exist, SHALL be registered in the `mkdocs.yml` navigation next to Authentication (an unregistered page fails the strict build), and SHALL carry the spec backlink every behaviour page carries. It SHALL be a runbook, not a second copy of `docs/authentication.md`: the normative prose about trusted headers stays there and this page links to it. It SHALL document (a) the three `local_login_policy` values and what each one closes, (b) the four host recovery commands with the exact invocation of **each** of the four for the standard image, the distroless image and Helm — including the username and provider-id arguments they take, (c) what to do when the identity provider is unreachable and when the second-factor secret is lost, and (d) the reverse-proxy consequence an operator meets first: behind a proxy an administrator MUST enrol two-factor before it can manage people, because step-up re-verification has no other factor to use and answers `403 step_up_unavailable`. It SHALL point at the reverse-proxy deployment sections instead of restating them, and SHALL contain no realistic-looking secret literal.

#### Scenario: The page builds and is reachable

- **WHEN** the docs workflow runs `mkdocs build --strict`
- **THEN** the build succeeds, `sso.md` is in the navigation, and every internal link and anchor it uses resolves

#### Scenario: A locked-out operator finds the way back

- **WHEN** an operator whose identity provider is down opens the page
- **THEN** it names the command that re-opens local sign-in and the command that resets a password, and states that both act on the database directly and need no environment variable

#### Scenario: The proxy admin learns the enrolment requirement before it bites

- **WHEN** an operator setting up a reverse-proxy install reads the page
- **THEN** it states that the administrator must enrol two-factor before it can manage people, and links to the section of the authentication page that governs step-up
