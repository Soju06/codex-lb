## MODIFIED Requirements

### Requirement: Dashboard settings persistence

The database SHALL persist dashboard settings, including weekly pace working days, the weekly pace gap smoothing window, reset-credit badge visibility, reset-credit action expiry-label visibility, and automatic reset-credit redemption before expiry.

#### Scenario: Dashboard settings persist reset-credit controls

- **WHEN** the database is migrated to the current head
- **THEN** `dashboard_settings` includes `show_reset_credit_badges`, `show_reset_credit_expiry_badge`, and `auto_redeem_reset_credits_before_expiry`
- **AND** existing rows default `show_reset_credit_badges` and `show_reset_credit_expiry_badge` to true
- **AND** existing rows default `auto_redeem_reset_credits_before_expiry` to false
