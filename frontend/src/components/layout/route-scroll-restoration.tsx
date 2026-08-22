import { useLayoutEffect, useRef } from "react";
import { NavigationType, useLocation, useNavigationType } from "react-router-dom";

export function RouteScrollRestoration() {
  const { hash, pathname } = useLocation();
  const navigationType = useNavigationType();
  const previousPathname = useRef(pathname);

  useLayoutEffect(() => {
    const pathnameChanged = previousPathname.current !== pathname;
    previousPathname.current = pathname;

    if (pathnameChanged && navigationType !== NavigationType.Pop && !hash) {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
  }, [hash, navigationType, pathname]);

  return null;
}
