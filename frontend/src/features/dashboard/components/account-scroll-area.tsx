import { ScrollArea } from "radix-ui";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

type AccountScrollAreaProps = {
  fullscreen: boolean;
  bordered?: boolean;
  children: ReactNode;
};

export function AccountScrollArea({ fullscreen, bordered = false, children }: AccountScrollAreaProps) {
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [viewportHeight, setViewportHeight] = useState(0);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!fullscreen || !viewport) {
      return;
    }

    const updateHeight = () => setViewportHeight(viewport.offsetHeight);
    updateHeight();
    const observer = new ResizeObserver(updateHeight);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [fullscreen]);

  useEffect(() => {
    const viewport = viewportRef.current;
    const shell = viewport?.closest<HTMLElement>("[data-dashboard-shell]");
    if (!fullscreen || !viewport || !shell) {
      return;
    }

    const forwardWheel = (event: WheelEvent) => {
      if (
        event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey ||
        !(event.target instanceof Node) || scrollAreaRef.current?.contains(event.target)
      ) {
        return;
      }

      const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE
        ? Number.parseFloat(getComputedStyle(viewport).lineHeight) || 16
        : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? viewport.clientHeight : 1;
      const previousTop = viewport.scrollTop;
      viewport.scrollTop += event.deltaY * unit;
      if (viewport.scrollTop !== previousTop) {
        event.preventDefault();
      }
    };

    // Listen on the DOM shell: portaled menus/dialogs keep their own scrolling.
    shell.addEventListener("wheel", forwardWheel, { passive: false });
    return () => shell.removeEventListener("wheel", forwardWheel);
  }, [fullscreen]);

  if (!fullscreen) {
    return children;
  }

  return (
    <ScrollArea.Root ref={scrollAreaRef} type="auto" className={cn("min-h-0 flex-1", bordered && "rounded-lg border bg-card")}>
      <ScrollArea.Viewport ref={viewportRef} className="h-full w-full rounded-[inherit]" tabIndex={0}>
        {children}
      </ScrollArea.Viewport>
      {/* Radix measures the track/content, not viewport-only resizes. Recreate only
          the fixed bar to refresh its metrics; keep the viewport and scroll offset. */}
      <ScrollArea.Scrollbar
        key={viewportHeight}
        orientation="vertical"
        className="z-[60] flex w-1.5 touch-none select-none"
        style={{ position: "fixed", top: 0, right: 0, bottom: 0 }}
      >
        <ScrollArea.Thumb className="relative flex-1 rounded-full bg-muted-foreground/40 hover:bg-muted-foreground/60" />
      </ScrollArea.Scrollbar>
      <ScrollArea.Scrollbar
        orientation="horizontal"
        className="flex h-1.5 touch-none flex-col select-none"
      >
        <ScrollArea.Thumb className="relative flex-1 rounded-full bg-muted-foreground/40 hover:bg-muted-foreground/60" />
      </ScrollArea.Scrollbar>
      <ScrollArea.Corner />
    </ScrollArea.Root>
  );
}
