## ADDED Requirements

### Requirement: Map `auto`/`default` enforced service tier to outbound omission
codex-lb accepts `auto`, `default`, `priority`, and `flex` (plus the `fast` alias for `priority`) at the API-key `enforced_service_tier` surface. The ChatGPT/Codex backend that the proxy forwards to, however, rejects `auto` and `default` as literal values (`Unsupported service_tier: <value>`); semantically both already mean "let upstream pick", which equals omitting the field.

When a request is enforced under an API key whose `enforced_service_tier` is `auto` or `default`, the proxy MUST forward the request with `service_tier` absent (`None`) rather than as the literal string. Enforcement of `priority` and `flex` MUST continue to forward the literal value unchanged.

#### Scenario: Enforced service tier is `default`
- **WHEN** a request is processed under an API key with `enforced_service_tier = "default"`
- **THEN** the outbound `service_tier` field is absent

#### Scenario: Enforced service tier is `auto`
- **WHEN** a request is processed under an API key with `enforced_service_tier = "auto"`
- **THEN** the outbound `service_tier` field is absent

#### Scenario: Enforced service tier is a real upstream tier
- **WHEN** a request is processed under an API key with `enforced_service_tier = "priority"` or `"flex"`
- **THEN** the outbound `service_tier` field equals the enforced value
