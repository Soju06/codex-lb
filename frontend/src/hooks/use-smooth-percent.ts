import { useEffect, useRef, useState } from "react";

import { useReducedMotion } from "@/hooks/use-reduced-motion";

const TWEEN_MS = 1_000;
const UNKNOWN_HOLD_MS = 90_000;

function clampPercent(value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  return Math.max(0, Math.min(100, value));
}

export function useSmoothPercent(value: number | null): {
  percent: number | null;
  everKnown: boolean;
} {
  const target = clampPercent(value);
  const reducedMotion = useReducedMotion();
  const [percent, setPercent] = useState<number | null>(target);
  const displayedRef = useRef<number | null>(target);

  useEffect(() => {
    if (target === null) {
      if (displayedRef.current === null) return undefined;

      const timeoutId = window.setTimeout(() => {
        displayedRef.current = null;
        setPercent(null);
      }, UNKNOWN_HOLD_MS);
      return () => window.clearTimeout(timeoutId);
    }

    const start = displayedRef.current;
    if (start === null || reducedMotion || Math.abs(start - target) < 0.01) {
      displayedRef.current = target;
      setPercent(target);
      return undefined;
    }

    let frameId = 0;
    const startedAt = performance.now();
    const tick = (now: number) => {
      const progress = Math.min(1, Math.max(0, (now - startedAt) / TWEEN_MS));
      const eased = 1 - Math.pow(1 - progress, 3);
      const next = start + (target - start) * eased;
      displayedRef.current = next;
      setPercent(next);
      if (progress < 1) frameId = requestAnimationFrame(tick);
    };

    frameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameId);
  }, [reducedMotion, target]);

  return { percent, everKnown: target !== null || percent !== null };
}
