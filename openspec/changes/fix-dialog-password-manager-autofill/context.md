# Context: password-manager suggestions over dialogs

## Purpose

Let password managers that render their suggestions into the page fill credentials
inside dashboard dialogs. The motivating case is Apple Passwords in Chrome on macOS.
Its verification-code suggestion showed in the sign-in TOTP dialog and the step-up
dialog but did nothing when chosen.

## Mechanism

A modal Radix `Dialog` mounts a `DismissableLayer` with
`disableOutsidePointerEvents`. That layer writes `pointer-events: none` inline on
`<body>`. Radix then opts its own surfaces back in: the overlay gets an inline
`pointer-events: auto`, and so does the content layer. The lock only matters for
elements Radix does not own. A browser extension's injected suggestion list is one
of them. It inherits `none`, so hit-testing skips it and the click lands on the
overlay below it, even though the list is painted on top.

Safari is unaffected because it draws Apple Passwords suggestions as native UI
outside the document. Pasting works everywhere because it never involves the
suggestion list.

## Decisions

- **Override the lock in CSS, scoped to an open dialog.**
  `body:has([data-slot="dialog-overlay"][data-state="open"])` sets
  `pointer-events: auto !important`. An author `!important` declaration outranks
  Radix's inline style. The rule only applies while a dashboard dialog's overlay is
  open, so overlay-less Radix modals elsewhere (menus, selects) keep their own
  behaviour when no dialog is open.
- **Make the overlay the only "outside".** With `<body>` interactive, a press on a
  third-party surface would reach Radix's outside-press handling and dismiss the
  dialog, so filling a code would close the form. The overlay covers the whole
  viewport, so every genuine outside press hits it. Anything else outside the
  content was drawn over the dialog by someone else.
- **Keep everything else Radix's modal provides.** Focus trapping, `aria-hidden`
  siblings and scroll locking are untouched. A click is delivered to the element
  under the pointer regardless of where focus moves, so the focus trap doesn't stop
  a suggestion from being chosen.
- **Rejected alternatives.** Rendering the sign-in code step as a page instead of a
  dialog fixes only that one surface. Making credential dialogs `modal={false}`
  fails when the step-up dialog opens above another modal dialog, which is its
  common case: the lower dialog still locks `<body>`. Targeting the extension's
  markup would depend on a third party's private DOM.

## Failure modes

- A third-party element placed under the overlay, rather than above it, is still
  unreachable. That is correct, because it sits behind the dialog.
- Sonner toasts sit outside the dialog. Pressing a toast no longer dismisses an
  open dialog, which matches the intent of acting on a toast.

## Example

1. In Chrome with the iCloud Passwords extension, sign in with a password to an
   account that has TOTP enabled.
2. The "Two-factor verification" dialog opens. Focus the code field and choose the
   Apple Passwords verification-code suggestion.
3. The six slots fill, the code auto-submits, and the dialog stays open until the
   verified session replaces it.
