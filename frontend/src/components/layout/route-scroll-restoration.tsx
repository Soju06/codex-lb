import { useLayoutEffect, useRef } from "react";
import { NavigationType, useLocation, useNavigationType } from "react-router-dom";

export function RouteScrollRestoration() {
  const { hash, pathname } = useLocation();
  const navigationType = useNavigationType();
  const previousPathname = useRef(pathname);
  const redirectsToHashTarget = pathname === "/firewall" || pathname === "/firewall/";

  useLayoutEffect(() => {
    const pathnameChanged = previousPathname.current !== pathname;
    previousPathname.current = pathname;

    if (
      pathnameChanged &&
      navigationType !== NavigationType.Pop &&
      !hash &&
      !redirectsToHashTarget
    ) {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
  }, [hash, navigationType, pathname, redirectsToHashTarget]);

  return null;
}
