import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { AppHeader } from "@/components/layout/app-header";

type HeaderProps = React.ComponentProps<typeof AppHeader>;

function renderHeader(props: Partial<HeaderProps> = {}) {
  return render(
    <MemoryRouter>
      <AppHeader onLogout={vi.fn()} {...props} />
    </MemoryRouter>,
  );
}

describe("AppHeader", () => {
  it("shows the aggregated available reset count badge on the Accounts link", () => {
    renderHeader({ availableResetCount: 5 });

    const accountsLink = screen.getByRole("link", { name: /Accounts/ });
    expect(within(accountsLink).getByTestId("nav-reset-badge")).toHaveTextContent("5");
  });

  it("hides the badge when there are no available resets", () => {
    renderHeader({ availableResetCount: 0 });

    expect(screen.queryByTestId("nav-reset-badge")).not.toBeInTheDocument();
  });
});
