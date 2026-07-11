## Context

`ModelMultiSelect` and `AccountMultiSelect` use Radix dropdown menus whose content is portalled outside the API-key dialog DOM subtree. In the deployed 1.20.1 dashboard, selecting a model reproducibly closes the parent edit dialog before Save. The selection state therefore cannot be persisted through the GUI.

## Goals

- A model or account dropdown interaction inside an API-key dialog must not dismiss that dialog.
- The operator must be able to select several models and submit the resulting allowlist.
- Clicking outside both the dialog and its owned dropdown content must continue to dismiss the dialog.
- The live repair must preserve the existing `codex-lb-data` volume and container runtime configuration.

## Non-goals

- Redesigning the multi-select controls.
- Changing API-key enforcement semantics or database schemas.
- Disabling all outside-click dismissal.
- Refactoring unrelated dialog or dropdown components.

## Decision

Add a narrowly scoped outside-interaction guard to the API-key create and edit dialog content. The guard prevents dialog dismissal only when the event target belongs to portalled dropdown content owned by the dialog. It does not globally disable outside interactions.

Configure the nested model and account dropdowns as non-modal. Radix otherwise composes two modal layers—a dropdown portal inside a dialog—and Chrome can dismiss the parent dialog during a checkbox selection even after the outside-interaction guard runs. The parent API-key dialog remains modal, so page content outside the editor is still protected.

The implementation will reuse the same guard for both API-key dialogs because both embed the same portalled selectors. A regression test will exercise the externally failing edit path: open the dialog, open Allowed models, select a model, assert that the dialog remains open, submit, and assert that the selected allowlist is present in the update payload.

## Deployment

Build a patched container from the exact deployed 1.20.1 revision. Before replacement, capture the existing container inspection and maintain the named `codex-lb-data` mount, published ports, restart policy, environment, and command. Replace only the application image, then verify health, persisted API-key state, and the original browser interaction.

## Failure handling

If the focused regression test does not fail before the code change, stop and refine the test rather than implementing. If the custom image does not become healthy, restore the prior container/image using the captured runtime configuration; the persistent volume remains untouched.
