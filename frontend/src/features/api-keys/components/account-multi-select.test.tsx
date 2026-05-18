import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { createAccountSummary } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

import { AccountMultiSelect } from "./account-multi-select";

describe("AccountMultiSelect", () => {
  it("shows available account limits inside the picker", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({
              accountId: "acc_quota",
              email: "quota@example.com",
              displayName: "Quota account",
              usage: {
                primaryRemainingPercent: 82,
                secondaryRemainingPercent: 67,
              },
            }),
          ],
        }),
      ),
    );

    const user = userEvent.setup();

    renderWithProviders(<AccountMultiSelect value={[]} onChange={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "All accounts" }));

    expect(await screen.findByText("5h 82% left")).toBeInTheDocument();
    expect(screen.getByText("7d 67% left")).toBeInTheDocument();
    expect(screen.queryByText(/GPT-5\.3-Codex-Spark/i)).not.toBeInTheDocument();
  });

  it("keeps account selection working with the richer rows", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    renderWithProviders(<AccountMultiSelect value={[]} onChange={onChange} />);

    await user.click(await screen.findByRole("button", { name: "All accounts" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: /primary@example\.com/i }));

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith(["acc_primary"]);
    });
  });
});
