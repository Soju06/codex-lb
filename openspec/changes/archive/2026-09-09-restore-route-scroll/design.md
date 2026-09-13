## Context

The SPA uses `BrowserRouter` around a persistent `AppLayout`; route pages render through its `Outlet`. Browser document navigation starts a destination at the top, but React Router keeps the existing window offset across client-side route changes unless the application supplies restoration behavior. Settings hash destinations already own their delayed target scrolling after advanced content mounts.

## Goals / Non-Goals

**Goals:**

- Match full-page behavior for pathname-changing client-side `PUSH` and `REPLACE` transitions without a hash.
- Leave `POP` history restoration, same-path query state, and hash-target navigation under their existing owners.
- Apply the rule once at the shared route shell for desktop and mobile links.

**Non-Goals:**

- Replacing browser history scroll restoration or persisting per-route offsets.
- Adding click handlers to header links.
- Changing the Settings advanced/hash logic, the legacy `/firewall` redirect, or route definitions.

## Decisions

- Add one renderless route-scroll component to `AppLayout`. It observes React Router's location and navigation type, retains only the previous pathname, and synchronously scrolls to `{ top: 0, left: 0, behavior: "auto" }` before paint when the pathname changed through `PUSH` or `REPLACE` and the destination has no hash. A shared shell listener avoids duplicated link behavior and covers programmatic navigation.
- Do not set `history.scrollRestoration` or act on `POP`; the browser remains responsible for back/forward positions. Do not act when only search parameters change, because dashboard filters encode view state in the query string without representing a new page.
- Treat any destination hash as an explicit scroll intent. React Router first exposes `/firewall` (or its trailing-slash equivalent `/firewall/`) as an intermediate `PUSH` location before its compatibility route replaces that entry with `/settings?advanced=1#firewall`, so the route shell also classifies those intermediaries as hash intent and does not reset them. The existing Settings target-scrolling path remains the only owner of the final Firewall position.
- Cover the navigation matrix with a focused React Router integration test and exercise both desktop and mobile header navigation against the built dashboard in Playwright.

Alternatives rejected: per-link `onClick` resets would duplicate policy and miss redirects or programmatic navigation; unconditional location resets would break back/forward, filters, and hash targets; React Router data-router `ScrollRestoration` would require replacing the current `BrowserRouter`/`Routes` architecture for one small behavior.

## Risks / Trade-offs

- **[Risk] A reset after paint can briefly show the stale offset** → use a layout effect so the destination is positioned before paint.
- **[Risk] Central handling can override intentional restoration** → gate on navigation type, pathname change, and absent hash, with regression coverage for every excluded case.
- **[Risk] Browser and JSDOM history behavior differ** → retain deterministic component tests and a built-app Playwright proof for actual scrolling and heading visibility.
