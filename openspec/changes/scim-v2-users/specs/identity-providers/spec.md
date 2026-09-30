## MODIFIED Requirements

### Requirement: Just-in-time usernames

These rules SHALL belong to every path that provisions an account from an external identity — sign-in resolution and SCIM alike — and SHALL exist once, as shared helpers, rather than once per path. The JIT username SHALL be `slugify(value)`: case-folded, `@` replaced by `.`, every character outside `[a-z0-9._-]` removed, cut to 56 characters; an empty result becomes `<prefix><first 8 hex of sha256(value)>`, where the prefix is a short marker of the path that provisioned the account — `th-` for the sign-in providers that already use it, so no existing account name changes, and its own marker for SCIM. The value slugified is the identity's subject, except on the SCIM path, where it is the pushed `userName`, because that is the name the identity provider means and a SCIM subject is an opaque external id. On a UNIQUE collision the resolver SHALL append `-2`, `-3`, … The username `admin` SHALL be treated as taken even before that account exists, so a proxy user named `admin` becomes `admin-2` and can never inherit the migrated break-glass account. A provisioned name SHALL be assigned once: a later change to the value at the identity provider SHALL NOT rename the account, because a rename driven by a machine push would race the install's own uniqueness rules and could move another account's name out from under it.

#### Scenario: Slug rules

- **WHEN** subjects `Alice.Smith`, `alice@example.com`, `al ice!`, `!!!` and `admin` are provisioned on an empty install
- **THEN** their usernames are `alice.smith`, `alice.example.com`, `alice`, `th-` + 8 hex characters, and `admin-2`

#### Scenario: The same rules on the provisioning path

- **WHEN** the same five values arrive as pushed `userName`s on an empty install
- **THEN** the local account names are the same, except that the empty slug takes the provisioning path's own prefix rather than `th-`
- **AND** both paths reach that answer through the same helpers rather than two copies of the rules
