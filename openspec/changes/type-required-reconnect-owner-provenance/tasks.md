## 1. Regression coverage

- [x] 1.1 Assert the existing soft-`1011` file-pin reconnect test receives
  `preferred_account_is_continuity_owner is True`.
- [x] 1.2 Assert the existing movable soft-`1011` reconnect test keeps
  `preferred_account_is_continuity_owner is False`.

## 2. Implementation

- [x] 2.1 Set reconnect `preferred_account_is_continuity_owner` from
  `required_preferred_account_id is not None`.
- [x] 2.2 Map typed `continuity_owner_unavailable` to the existing
  required-owner unavailable envelope whenever that required owner exists.

## 3. Single-account override

- [x] 3.1 Add `preferred_account_overrides_single_account_routing` and skip
  single-account narrowing only for required preferred ownership.
- [x] 3.2 Pass the override from reconnect only when
  `request_state.file_required_preferred_account` is set.
- [x] 3.3 Forward the optional kwarg through stream selection compatibility.
- [x] 3.4 Assert file-pin continuity plus override selects the owner without
  dashboard `account_ids` narrowing; previous-response/account-neutral
  conflict and assignment-scope conflict remain.

## 4. Validation

- [x] 4.1 Run the focused HTTP-bridge reconnect unit tests.
- [x] 4.2 Run a driver that prints file-pin reconnect kwargs and a typed
  owner-miss envelope.
- [x] 4.3 Run focused Ruff, ty, and strict OpenSpec validation for this change.
