import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { OAuthLivePolicyCard } from "@/features/accounts/components/oauth-live-policy-card";
import { createAccountSummary } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

describe("OAuthLivePolicyCard", () => {
  it("requires an explicit account before saving an active policy", async () => {
    const putBody = vi.fn();
    server.use(
      http.get("/api/accounts/acc_caller/oauth-live-policy", () =>
        HttpResponse.json({
          callerAccountId: "acc_caller",
          isActive: false,
          allowedAccountIds: [],
        }),
      ),
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({
              accountId: "acc_upstream",
              email: "upstream@example.com",
              displayName: "Upstream",
            }),
          ],
        }),
      ),
      http.put("/api/accounts/acc_caller/oauth-live-policy", async ({ request }) => {
        const body = await request.json();
        putBody(body);
        return HttpResponse.json({ callerAccountId: "acc_caller", ...(body as object) });
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<OAuthLivePolicyCard accountId="acc_caller" />);

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

  it("keeps every policy control disabled for read-only users", async () => {
    server.use(
      http.get("/api/accounts/acc_caller/oauth-live-policy", () =>
        HttpResponse.json({
          callerAccountId: "acc_caller",
          isActive: true,
          allowedAccountIds: ["acc_upstream"],
        }),
      ),
      http.get("/api/accounts", () => HttpResponse.json({ accounts: [] })),
    );

    renderWithProviders(<OAuthLivePolicyCard accountId="acc_caller" readOnly />);

    const toggle = await screen.findByRole("switch", { name: "Enable OAuth Live Voice" });
    await waitFor(() => expect(toggle).toBeChecked());
    expect(toggle).toBeDisabled();
    expect(screen.getByRole("button", { name: "Allowed upstream accounts" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});
