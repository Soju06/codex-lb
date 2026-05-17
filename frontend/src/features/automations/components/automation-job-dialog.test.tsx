import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { AutomationJob } from "@/features/automations/schemas";
import { createAccountSummary } from "@/test/mocks/factories";
import { server } from "@/test/mocks/server";
import { renderWithProviders } from "@/test/utils";

import { AutomationJobDialog } from "./automation-job-dialog";

if (!HTMLElement.prototype.hasPointerCapture) {
  Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
    configurable: true,
    value: () => false,
  });
}

if (!HTMLElement.prototype.setPointerCapture) {
  Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
    configurable: true,
    value: () => {},
  });
}

if (!HTMLElement.prototype.releasePointerCapture) {
  Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
    configurable: true,
    value: () => {},
  });
}

if (!HTMLElement.prototype.scrollIntoView) {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: () => {},
  });
}

describe("AutomationJobDialog", () => {
  it("does not show fallback reasoning efforts when the selected model exposes an empty supported list", async () => {
    renderWithProviders(
      <AutomationJobDialog
        open
        busy={false}
        editingJob={null}
        models={[
          {
            id: "gpt-4o-mini",
            name: "GPT 4o Mini",
            supportedReasoningEfforts: [],
            defaultReasoningEffort: null,
          },
        ]}
        modelsLoading={false}
        onOpenChange={vi.fn()}
        onCreate={vi.fn().mockResolvedValue(undefined)}
        onUpdate={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByLabelText("Reasoning effort")).toBeInTheDocument();
    expect(screen.queryByText("Low")).not.toBeInTheDocument();
    expect(screen.queryByText("Medium")).not.toBeInTheDocument();
    expect(screen.queryByText("High")).not.toBeInTheDocument();
    expect(screen.queryByText("XHigh")).not.toBeInTheDocument();
  });

  it("clears a stored unsupported reasoning effort when the dialog normalizes it to model default", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({
              accountId: "acc_primary",
              email: "primary@example.com",
              displayName: "Primary account",
            }),
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    const onUpdate = vi.fn().mockResolvedValue(undefined);
    const editingJob: AutomationJob = {
      id: "job_legacy_reasoning",
      name: "Legacy reasoning job",
      enabled: true,
      includePausedAccounts: false,
      schedule: {
        type: "daily",
        time: "05:00",
        timezone: "UTC",
        thresholdMinutes: 0,
        days: ["mon", "wed", "fri"],
      },
      model: "gpt-4o-mini",
      reasoningEffort: "low",
      prompt: "ping",
      accountIds: ["acc_primary"],
      nextRunAt: "2026-04-23T05:00:00Z",
      lastRun: null,
    };

    renderWithProviders(
      <AutomationJobDialog
        open
        busy={false}
        editingJob={editingJob}
        models={[
          {
            id: "gpt-4o-mini",
            name: "GPT 4o Mini",
            supportedReasoningEfforts: [],
            defaultReasoningEffort: null,
          },
        ]}
        modelsLoading={false}
        onOpenChange={vi.fn()}
        onCreate={vi.fn().mockResolvedValue(undefined)}
        onUpdate={onUpdate}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
    });

    const nameInput = screen.getByLabelText("Name");
    await user.clear(nameInput);
    await user.type(nameInput, "Legacy reasoning job renamed");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(onUpdate).toHaveBeenCalledTimes(1);
    });

    const [, payload] = onUpdate.mock.calls[0];
    expect(payload).toMatchObject({
      name: "Legacy reasoning job renamed",
      model: "gpt-4o-mini",
      prompt: "ping",
      reasoningEffort: null,
    });
  });

  it("submits a user-selected reasoning effort on create", async () => {
    server.use(
      http.get("/api/accounts", () =>
        HttpResponse.json({
          accounts: [
            createAccountSummary({
              accountId: "acc_primary",
              email: "primary@example.com",
              displayName: "Primary account",
            }),
          ],
        }),
      ),
    );

    const user = userEvent.setup();
    const onCreate = vi.fn().mockResolvedValue(undefined);

    renderWithProviders(
      <AutomationJobDialog
        open
        busy={false}
        editingJob={null}
        models={[
          {
            id: "gpt-5.4",
            name: "GPT 5.4",
            supportedReasoningEfforts: ["low", "medium", "high"],
            defaultReasoningEffort: "medium",
          },
        ]}
        modelsLoading={false}
        onOpenChange={vi.fn()}
        onCreate={onCreate}
        onUpdate={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    const nameInput = screen.getByLabelText("Name");
    await user.type(nameInput, "Create automation with low reasoning");
    await user.click(screen.getByLabelText("Reasoning effort"));
    const lowOptionLabel = (await screen.findAllByText("Low")).find((node) => node.closest("[role='option']") !== null);
    if (!lowOptionLabel) {
      throw new Error("Visible Low reasoning option not found");
    }
    await user.click(lowOptionLabel.closest("[role='option']") as HTMLElement);
    await user.click(screen.getByRole("button", { name: "Create automation" }));

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledTimes(1);
    });

    expect(onCreate.mock.calls[0][0]).toMatchObject({
      name: "Create automation with low reasoning",
      model: "gpt-5.4",
      reasoningEffort: "low",
    });
  });
});
