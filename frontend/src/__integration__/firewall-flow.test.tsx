import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/mocks/server";

describe("firewall flow integration", () => {
  it("loads firewall page and performs add/remove", async () => {
    const user = userEvent.setup({ delay: null });
    const entries: Array<{ ipAddress: string; createdAt: string }> = [];
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    server.use(
      http.get("/api/dashboard-auth/session", () =>
        HttpResponse.json({
          authenticated: true,
          passwordRequired: true,
          totpRequiredOnLogin: false,
          totpConfigured: true,
        }),
      ),
      http.get("/api/firewall/ips", () =>
        HttpResponse.json({
          mode: entries.length === 0 ? "allow_all" : "allowlist_active",
          entries,
        }),
      ),
      http.post("/api/firewall/ips", async ({ request }) => {
        const payload = (await request.json()) as { ipAddress?: string };
        const ipAddress = String(payload.ipAddress || "").trim();
        const createdAt = "2026-02-18T12:00:00Z";
        entries.push({ ipAddress, createdAt });
        return HttpResponse.json({ ipAddress, createdAt });
      }),
      http.delete("/api/firewall/ips/:ipAddress", ({ params }) => {
        const ipAddress = decodeURIComponent(String(params.ipAddress));
        const index = entries.findIndex((entry) => entry.ipAddress === ipAddress);
        if (index >= 0) {
          entries.splice(index, 1);
        }
        return HttpResponse.json({ status: "deleted" });
      }),
    );

    window.history.pushState({}, "", "/firewall");
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Firewall" })).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("127.0.0.1 or 2001:db8::1"), "127.0.0.1");
    await user.click(screen.getByRole("button", { name: "Add IP" }));

    expect(await screen.findByText("127.0.0.1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(screen.queryByText("127.0.0.1")).not.toBeInTheDocument();
    });

    confirmSpy.mockRestore();
  });
});
