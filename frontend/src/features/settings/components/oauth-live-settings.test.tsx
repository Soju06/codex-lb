import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { OAuthLiveSettings } from "@/features/settings/components/oauth-live-settings";
import { createAccountSummary } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

const upstream = createAccountSummary({
  accountId: "acc_upstream",
  email: "upstream@example.com",
  displayName: "Upstream",
});

describe("OAuthLiveSettings", () => {
  it("shows one global policy without a caller-account control", async () => {
    renderWithProviders(<OAuthLiveSettings />);

    expect(await screen.findByRole("switch", { name: "Enable OAuth Live Voice" })).toBeInTheDocument();
    const accountTrigger = screen.getByRole("button", { name: "Allowed upstream accounts" });
    expect(accountTrigger).toBeInTheDocument();
    expect(accountTrigger.closest('[data-slot="oauth-live-account-control"]')).toHaveClass("col-start-2");
    expect(screen.queryByRole("textbox", { name: /caller account/i })).not.toBeInTheDocument();
  });

  it("saves the global enable state and shared upstream pool", async () => {
    const putBody = vi.fn();
    server.use(
      http.get("/api/oauth-live-policy", () =>
        HttpResponse.json({ isActive: false, allowedAccountIds: [] }),
      ),
      http.get("/api/accounts", () => HttpResponse.json({ accounts: [upstream] })),
      http.put("/api/oauth-live-policy", async ({ request }) => {
        const body = await request.json();
        putBody(body);
        return HttpResponse.json(body as object);
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<OAuthLiveSettings />);

    await user.click(await screen.findByRole("switch", { name: "Enable OAuth Live Voice" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Select at least one allowed account");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Allowed upstream accounts" }));
    expect(screen.queryByRole("menuitemcheckbox", { name: "All accounts" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitemcheckbox", { name: /upstream@example\.com/i }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(putBody).toHaveBeenCalledWith({
        isActive: true,
        allowedAccountIds: ["acc_upstream"],
      });
    });
  });

  it("disables every mutation for read-only users", async () => {
    server.use(
      http.get("/api/oauth-live-policy", () =>
        HttpResponse.json({ isActive: true, allowedAccountIds: ["acc_upstream"] }),
      ),
      http.get("/api/accounts", () => HttpResponse.json({ accounts: [upstream] })),
    );

    renderWithProviders(<OAuthLiveSettings readOnly />);

    const toggle = await screen.findByRole("switch", { name: "Enable OAuth Live Voice" });
    await waitFor(() => expect(toggle).toBeChecked());
    expect(toggle).toBeDisabled();
    expect(screen.getByRole("button", { name: "Allowed upstream accounts" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});
