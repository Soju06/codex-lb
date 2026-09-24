# TOTP digit normalization

The scope is invalid-code handling in the shared verifier and dashboard routes.
Python classifies fullwidth and Arabic-Indic numerals as digits, whereas the
string form of `hmac.compare_digest` accepts ASCII only. Filtering to ASCII
preserves the existing formatting policy without adding transliteration.

For example, `１２３４５６` previously raised `TypeError`; it now follows the
normal invalid-code response. A current ASCII code written as `123 456` retains
its existing behavior. Rejected input must not consume a replay step or enroll
a secret. No migration or operator action is needed.
