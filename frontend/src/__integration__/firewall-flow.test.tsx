import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import App from "@/App";
import { renderWithProviders } from "@/test/utils";
import { server } from "@/test/mocks/server";

describe("firewall flow integration", () => {
  it("loads firewall section in settings and performs add/remove", async () => {
    const user = userEvent.setup({ delay: null });
    const entries: Array<{ ipAddress: string; createdAt: string }> = [];

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

    window.history.pushState({}, "", "/settings");
    renderWithProviders(<App />);

    const firewallHeading = await screen.findByRole("heading", { name: "Firewall" });
    expect(firewallHeading).toBeInTheDocument();

    // Scope queries to the firewall section
    const firewallSection = firewallHeading.closest("section")!;
    const fw = within(firewallSection);

    await user.type(fw.getByPlaceholderText("127.0.0.1 or 2001:db8::1"), "127.0.0.1");
    await user.click(fw.getByRole("button", { name: "Add IP" }));

    expect(await fw.findByText("127.0.0.1")).toBeInTheDocument();

    await user.click(fw.getByRole("button", { name: "Remove" }));

    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(screen.queryByText("127.0.0.1")).not.toBeInTheDocument();
    });
  });
});
