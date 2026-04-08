import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PlatformIdentityDialog } from "@/features/accounts/components/platform-identity-dialog";
import { createAccountSummary } from "@/test/mocks/factories";

describe("PlatformIdentityDialog", () => {
  it("submits a provider-aware platform identity payload with zero enabled routes by default", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();

    render(
      <PlatformIdentityDialog
        open
        busy={false}
        error={null}
        mode="create"
        prerequisiteSatisfied
        onOpenChange={onOpenChange}
        onSubmit={onSubmit}
      />,
    );

    await user.type(screen.getByLabelText("Label"), "Production Platform");
    await user.type(screen.getByLabelText("API key"), "sk-platform-test");
    await user.type(screen.getByLabelText("Organization"), "org_test");
    await user.type(screen.getByLabelText("Project"), "proj_test");

    expect(screen.getByText(/Register a fallback-only upstream identity for/i)).toBeInTheDocument();
    expect(screen.getAllByText("/v1/models").length).toBeGreaterThan(0);
    expect(screen.getAllByText("/v1/responses").length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        "Allow this identity to handle stateless HTTP Responses API calls only. Stateless HTTP only; compact, chat completions, websocket, and continuity-bound requests stay on ChatGPT.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No route families enabled. This identity stays unroutable until you opt into one."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Requires an existing ChatGPT account that is not paused or deactivated. Only one Platform API key can be registered, and it is used only for /v1/models plus stateless HTTP /v1/responses fallback.",
      ),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add API key" }));

    expect(onSubmit).toHaveBeenCalledWith({
      label: "Production Platform",
      apiKey: "sk-platform-test",
      organization: "org_test",
      project: "proj_test",
      eligibleRouteFamilies: [],
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("blocks submission when no active ChatGPT account is available", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    render(
      <PlatformIdentityDialog
        open
        busy={false}
        error={null}
        mode="create"
        prerequisiteSatisfied={false}
        onOpenChange={() => {}}
        onSubmit={onSubmit}
      />,
    );

    await user.type(screen.getByLabelText("Label"), "Platform Key");
    await user.type(screen.getByLabelText("API key"), "sk-platform-test");

    expect(
      screen.getByText("Add or reactivate a ChatGPT account first. Platform keys cannot be used on their own."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add API key" })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits only changed fields in edit mode and allows clearing org/project without replacing the key", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();

    render(
      <PlatformIdentityDialog
        open
        busy={false}
        error={null}
        mode="edit"
        account={createAccountSummary({
          accountId: "platform_1",
          email: "Platform Original",
          displayName: "Platform Original",
          label: "Platform Original",
          planType: "openai_platform",
          providerKind: "openai_platform",
          routingSubjectId: "platform_1",
          organization: "org_original",
          project: "proj_original",
          eligibleRouteFamilies: ["public_models_http", "public_responses_http"],
          usage: null,
          auth: null,
        })}
        onOpenChange={onOpenChange}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(
      screen.getByText(
        "Leave blank to keep the current Platform API key. Enter a new key only when rotating credentials.",
      ),
    ).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Label"));
    await user.type(screen.getByLabelText("Label"), "Platform Renamed");
    await user.clear(screen.getByLabelText("Organization"));
    await user.clear(screen.getByLabelText("Project"));
    await user.click(screen.getAllByRole("checkbox")[0]);
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(onSubmit).toHaveBeenCalledWith({
      label: "Platform Renamed",
      organization: null,
      project: null,
      eligibleRouteFamilies: ["public_responses_http"],
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("allows editing even when no active ChatGPT account is currently available", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);

    render(
      <PlatformIdentityDialog
        open
        busy={false}
        error={null}
        mode="edit"
        prerequisiteSatisfied={false}
        account={createAccountSummary({
          accountId: "platform_2",
          email: "Platform Original",
          displayName: "Platform Original",
          label: "Platform Original",
          planType: "openai_platform",
          providerKind: "openai_platform",
          routingSubjectId: "platform_2",
          eligibleRouteFamilies: [],
          usage: null,
          auth: null,
        })}
        onOpenChange={() => {}}
        onSubmit={onSubmit}
      />,
    );

    expect(
      screen.getByText(
        "Only /v1/models and stateless HTTP /v1/responses can ever use this key. ChatGPT-only, compact, websocket, and continuity-bound requests stay on ChatGPT.",
      ),
    ).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Label"));
    await user.type(screen.getByLabelText("Label"), "Platform Still Editable");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(onSubmit).toHaveBeenCalledWith({ label: "Platform Still Editable" });
  });
});
