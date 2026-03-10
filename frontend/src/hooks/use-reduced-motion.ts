import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function getMatch(): boolean {
  return typeof window !== "undefined" && window.matchMedia(QUERY).matches;
}

/** Returns `true` when the user (or Playwright) prefers reduced motion. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(getMatch);

  useEffect(() => {
    const mql = window.matchMedia(QUERY);
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  return reduced;
}
