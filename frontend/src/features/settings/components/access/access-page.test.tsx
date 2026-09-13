import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { AccessPage } from "@/features/settings/components/access/access-page";
import { renderAt, signInAsTeamAdmin } from "@/test/access-test-utils";
import { OPERATOR_PERMISSIONS, createAccessSummary } from "@/test/mocks/factories";

describe("AccessPage (/settings/access)", () => {
  beforeEach(() => {
    signInAsTeamAdmin();
  });

  it("renders the people table full-page for signed-in users:manage holders on any tier", async () => {
    signInAsTeamAdmin({ accessSummary: createAccessSummary(), tier: "individual" });

    renderAt(<AccessPage />, "/settings/access");

    expect(screen.getByRole("heading", { name: "Access" })).toBeInTheDocument();
    expect(await screen.findByTestId("people-row-admin")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Settings" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("link", { name: "Change in My sign-in" })).toHaveAttribute("href", "/settings#access");
    expect(screen.queryByText("View full page")).not.toBeInTheDocument();
    expect(await screen.findByText("Two-factor is not required for everyone at sign-in.")).toBeInTheDocument();
  });

  it.each([
    ["without users:manage", { permissions: OPERATOR_PERMISSIONS }],
    ["without an account (implicit admin)", { user: null }],
    ["without an account on a trusted-header install", { user: null, authMode: "trusted_header" as const }],
    ["without an account when auth is disabled", { user: null, authMode: "disabled" as const }],
  ])("sends a principal %s back to Settings", (_label, overrides) => {
    signInAsTeamAdmin(overrides);

    renderAt(<AccessPage />, "/settings/access");

    expect(screen.getByTestId("location")).toHaveTextContent("/settings");
    expect(screen.queryByRole("heading", { name: "Access" })).not.toBeInTheDocument();
  });
});
