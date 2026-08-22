import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouteScrollRestoration } from "@/components/layout/route-scroll-restoration";

function NavigationHarness() {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <>
      <RouteScrollRestoration />
      <output aria-label="location">{`${location.pathname}${location.search}${location.hash}`}</output>
      <button type="button" onClick={() => void navigate("/reports")}>
        Push pathname
      </button>
      <button type="button" onClick={() => void navigate("/accounts", { replace: true })}>
        Replace pathname
      </button>
      <button type="button" onClick={() => void navigate(`${location.pathname}?filter=active`)}>
        Change query
      </button>
      <button type="button" onClick={() => void navigate("/settings?advanced=1#firewall")}>
        Open hash target
      </button>
      <button type="button" onClick={() => void navigate("/firewall")}>
        Open legacy hash redirect
      </button>
      <button type="button" onClick={() => void navigate("/firewall/")}>
        Open trailing-slash legacy hash redirect
      </button>
      <button type="button" onClick={() => void navigate(-1)}>
        Go back
      </button>
      <Routes>
        <Route path="/firewall" element={<Navigate to="/settings?advanced=1#firewall" replace />} />
        <Route path="*" element={null} />
      </Routes>
    </>
  );
}

function renderHarness(initialEntries: string[] = ["/dashboard"], initialIndex = initialEntries.length - 1) {
  return render(
    <MemoryRouter initialEntries={initialEntries} initialIndex={initialIndex}>
      <NavigationHarness />
    </MemoryRouter>,
  );
}

describe("RouteScrollRestoration", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["PUSH", "Push pathname", "/reports"],
    ["REPLACE", "Replace pathname", "/accounts"],
  ])("resets a pathname-changing %s navigation", async (_navigationType, action, destination) => {
    const user = userEvent.setup({ delay: null });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    renderHarness();

    await user.click(screen.getByRole("button", { name: action }));

    expect(await screen.findByRole("status", { name: "location" })).toHaveTextContent(destination);
    expect(scrollTo).toHaveBeenCalledOnce();
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, left: 0, behavior: "auto" });
  });

  it("preserves the current position for a same-path query change", async () => {
    const user = userEvent.setup({ delay: null });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    renderHarness();

    await user.click(screen.getByRole("button", { name: "Change query" }));

    expect(await screen.findByRole("status", { name: "location" })).toHaveTextContent("/dashboard?filter=active");
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("leaves a pathname-changing hash destination to its target owner", async () => {
    const user = userEvent.setup({ delay: null });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    renderHarness();

    await user.click(screen.getByRole("button", { name: "Open hash target" }));

    expect(await screen.findByRole("status", { name: "location" })).toHaveTextContent(
      "/settings?advanced=1#firewall",
    );
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it.each([
    ["canonical", "Open legacy hash redirect"],
    ["trailing-slash", "Open trailing-slash legacy hash redirect"],
  ])("preserves the hash intent of the %s in-app Firewall compatibility redirect", async (_path, action) => {
    const user = userEvent.setup({ delay: null });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    renderHarness();

    await user.click(screen.getByRole("button", { name: action }));

    expect(await screen.findByRole("status", { name: "location" })).toHaveTextContent(
      "/settings?advanced=1#firewall",
    );
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("leaves POP navigation to browser history restoration", async () => {
    const user = userEvent.setup({ delay: null });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    renderHarness(["/settings", "/dashboard"]);

    await user.click(screen.getByRole("button", { name: "Go back" }));

    expect(await screen.findByRole("status", { name: "location" })).toHaveTextContent("/settings");
    expect(scrollTo).not.toHaveBeenCalled();
  });
});
