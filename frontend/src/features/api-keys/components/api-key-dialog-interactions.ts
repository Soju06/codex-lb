type OutsideInteractionEvent = {
  target: EventTarget | null;
  preventDefault(): void;
};

export function preventApiKeyDialogDropdownDismiss(event: OutsideInteractionEvent): void {
  if (
    event.target instanceof Element &&
    event.target.closest('[data-slot="dropdown-menu-content"]')
  ) {
    event.preventDefault();
  }
}
