import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/utils";
import type { TeamMember } from "@/features/team/schemas";

import { TeamPage } from "./team-page";

const hookMocks = vi.hoisted(() => ({
  useTeamMembers: vi.fn(),
  useTeamOnboarding: vi.fn(),
}));

vi.mock("@/features/team/hooks/use-team", () => hookMocks);

type MutationMock = {
  isPending: boolean;
  error: Error | null;
  mutateAsync: ReturnType<typeof vi.fn>;
};

function createMutationMock(): MutationMock {
  return { isPending: false, error: null, mutateAsync: vi.fn() };
}

function createMember(overrides: Partial<TeamMember> = {}): TeamMember {
  return {
    id: "member-1",
    name: "Ada",
    email: "ada@example.com",
    status: "active",
    costCapDayUsd: 5,
    costCapWeekUsd: null,
    costCapMonthUsd: null,
    tokenCapDay: null,
    tokenCapWeek: null,
    tokenCapMonth: null,
    allowedModels: null,
    notes: null,
    createdAt: "2026-09-18T10:00:00Z",
    updatedAt: "2026-09-18T10:00:00Z",
    usage: {
      day: { costUsd: 1.2, tokens: 1_000 },
      week: { costUsd: 4, tokens: 5_000 },
      month: { costUsd: 9, tokens: 20_000 },
    },
    gate: "ok",
    keys: [],
    ...overrides,
  };
}

function renderTeamPage({
  members = [createMember()],
  membersQuery = {
    data: members,
    error: null as Error | null,
    isPending: false,
    isFetching: false,
    refetch: vi.fn(),
  },
  createMutation = createMutationMock(),
  updateMutation = createMutationMock(),
  deleteMutation = createMutationMock(),
  issueKeyMutation = createMutationMock(),
}: {
  members?: TeamMember[];
  membersQuery?: {
    data: TeamMember[] | undefined;
    error: Error | null;
    isPending: boolean;
    isFetching: boolean;
    refetch: ReturnType<typeof vi.fn>;
  };
  createMutation?: MutationMock;
  updateMutation?: MutationMock;
  deleteMutation?: MutationMock;
  issueKeyMutation?: MutationMock;
} = {}) {
  hookMocks.useTeamMembers.mockReturnValue({
    membersQuery,
    createMutation,
    updateMutation,
    deleteMutation,
    issueKeyMutation,
  });
  hookMocks.useTeamOnboarding.mockReturnValue({ data: undefined, error: null, isPending: false });
  return renderWithProviders(<TeamPage />);
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("TeamPage", () => {
  it("renders members with usage against their caps", () => {
    renderTeamPage();

    expect(screen.getByRole("heading", { name: "Team" })).toBeInTheDocument();
    const row = screen.getByTestId("team-member-row-member-1");
    expect(within(row).getByText("Ada")).toBeInTheDocument();
    expect(within(row).getByText("ada@example.com")).toBeInTheDocument();
    expect(within(row).getByText("$1.20 / $5.00")).toBeInTheDocument();
    expect(within(row).getByText("$4.00 / no cap")).toBeInTheDocument();
  });

  it("shows the empty state when there are no members", () => {
    renderTeamPage({ members: [] });

    expect(screen.getByText("No team members yet")).toBeInTheDocument();
  });

  it("renders every gate chip state", () => {
    renderTeamPage({
      members: [
        createMember({ id: "m-ok", name: "Ok", gate: "ok" }),
        createMember({ id: "m-near", name: "Near", gate: "near_cap" }),
        createMember({ id: "m-over", name: "Over", gate: "over_cap" }),
        createMember({ id: "m-susp", name: "Suspended", gate: "suspended", status: "suspended" }),
      ],
    });

    expect(within(screen.getByTestId("team-member-row-m-ok")).getByText("OK")).toBeInTheDocument();
    expect(
      within(screen.getByTestId("team-member-row-m-near")).getByText("Near cap"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("team-member-row-m-over")).getByText("Over cap"),
    ).toBeInTheDocument();

    const suspendedRow = screen.getByTestId("team-member-row-m-susp");
    expect(within(suspendedRow).getAllByText("Suspended").length).toBeGreaterThan(0);
  });

  it("reveals the plaintext key once after issuing a key", async () => {
    const user = userEvent.setup();
    const issueKeyMutation = createMutationMock();
    issueKeyMutation.mutateAsync.mockResolvedValue({ id: "key-1", key: "sk-clb-secret-value" });

    renderTeamPage({ issueKeyMutation });

    await user.click(screen.getByRole("button", { name: "Actions for Ada" }));
    await user.click(screen.getByRole("menuitem", { name: "Issue key" }));

    await waitFor(() => {
      expect(issueKeyMutation.mutateAsync).toHaveBeenCalledWith({ memberId: "member-1" });
    });

    const dialog = await screen.findByRole("dialog", { name: "API key created" });
    expect(within(dialog).getByText("sk-clb-secret-value")).toBeInTheDocument();
  });

  it("opens the edit drawer prefilled with the member's caps", async () => {
    const user = userEvent.setup();
    renderTeamPage();

    await user.click(screen.getByRole("button", { name: "Actions for Ada" }));
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));

    const drawer = await screen.findByRole("dialog", { name: "Edit team member" });
    expect(within(drawer).getByLabelText("Name")).toHaveValue("Ada");
    expect(within(drawer).getByLabelText("Cost cap / day ($)")).toHaveValue(5);
  });

  it("surfaces list errors", () => {
    renderTeamPage({
      membersQuery: {
        data: undefined,
        error: new Error("boom list"),
        isPending: false,
        isFetching: false,
        refetch: vi.fn(),
      },
      members: [],
    });

    expect(screen.getByText("boom list")).toBeInTheDocument();
  });
});
