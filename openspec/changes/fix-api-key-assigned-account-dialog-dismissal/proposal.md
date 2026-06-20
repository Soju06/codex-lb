# fix-api-key-assigned-account-dialog-dismissal

## Summary

Fix the API key edit dialog so selecting assigned accounts does not make the dialog dismiss before the operator can save.

## Motivation

The Assigned accounts picker is rendered inside the edit dialog but uses a portaled dropdown. After selecting an account, subsequent clicks can be treated as an outside interaction by the dialog stack, closing the edit dialog and discarding the pending assignment change before Save can be clicked.

## Scope

- Frontend API key edit/create account picker interaction only.
- No backend contract or database changes.
