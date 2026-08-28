"use client"

import * as React from "react"

// Floating layers (Radix Select / DropdownMenu / Popover, or anything rendered
// through a Radix popper) that can appear inside a modal. Clicking inside one —
// or outside one while it is open — must dismiss the LAYER, never the modal.
const FLOATING_LAYER_SELECTOR = [
  "[data-slot='select-content']",
  "[data-slot='dropdown-menu-content']",
  "[data-slot='dropdown-menu-sub-content']",
  "[data-slot='popover-content']",
  "[data-radix-popper-content-wrapper]",
].join(", ")

const OPEN_FLOATING_LAYER_SELECTOR = [
  "[data-slot='select-content'][data-state='open']",
  "[data-slot='dropdown-menu-content'][data-state='open']",
  "[data-slot='dropdown-menu-sub-content'][data-state='open']",
  "[data-slot='popover-content'][data-state='open']",
].join(", ")

function isFloatingLayerTarget(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(FLOATING_LAYER_SELECTOR) !== null
}

function isFloatingLayerOpen(): boolean {
  return (
    typeof document !== "undefined" &&
    document.querySelector(OPEN_FLOATING_LAYER_SELECTOR) !== null
  )
}

// Minimal shape shared by Radix's FocusOutside / PointerDownOutside /
// InteractOutside events.
type DismissEvent = {
  target: EventTarget | null
  defaultPrevented: boolean
  preventDefault: () => void
}

/**
 * Guard for a modal's onFocusOutside / onInteractOutside / onPointerDownOutside:
 * keeps the modal open when the "outside" interaction is really the user
 * dismissing a nested floating layer (Select / DropdownMenu / Popover / custom
 * multi-select), instead of closing the modal.
 *
 * The captured-at-pointerdown flag is essential: a mouse click outside an OPEN
 * dropdown makes Radix close that dropdown FIRST (its data-state flips to
 * "closed") before the modal's outside handler runs, so a live DOM check would
 * already miss it and the modal would wrongly close. We capture the open-state
 * in a document pointerdown listener on the CAPTURE phase, which runs before
 * Radix's own bubble-phase dismiss listeners.
 */
export function useFloatingLayerDismissGuard() {
  const floatingLayerOpenAtPointerDownRef = React.useRef(false)

  React.useEffect(() => {
    const onPointerDownCapture = () => {
      floatingLayerOpenAtPointerDownRef.current = isFloatingLayerOpen()
    }
    document.addEventListener("pointerdown", onPointerDownCapture, true)
    return () =>
      document.removeEventListener("pointerdown", onPointerDownCapture, true)
  }, [])

  return React.useCallback((event: DismissEvent) => {
    if (
      !event.defaultPrevented &&
      (isFloatingLayerTarget(event.target) ||
        floatingLayerOpenAtPointerDownRef.current ||
        isFloatingLayerOpen())
    ) {
      event.preventDefault()
    }
  }, [])
}
