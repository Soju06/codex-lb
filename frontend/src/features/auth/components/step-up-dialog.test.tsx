import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StepUpDialog } from "@/features/auth/components/step-up-dialog";
import { useAuthStore } from "@/features/auth/hooks/use-auth";
import { post, setStepUpHandlers } from "@/lib/api-client";
import { server } from "@/test/mocks/server";
import { z } from "zod";

const okSchema = z.object({ status: z.string() });

function gated(methods: string[]) {
  // First call: the gate; after a step-up the same route answers 200.
  let stepped = false;
  server.use(
    http.post("/api/dashboard-auth/step-up", () => {
      stepped = true;
      return HttpResponse.json({ verifiedAt: 1, expiresAt: 301 });
    }),
    http.post("/api/guarded", () =>
      stepped
        ? HttpResponse.json({ status: "ok" })
        : HttpResponse.json(
            { error: { code: "step_up_required", message: "Confirm", param: "security:write", details: { methods } } },
            { status: 403 },
          ),
    ),
  );
}

describe("StepUpDialog", () => {
  beforeEach(() => {
    useAuthStore.setState({ refreshSession: vi.fn().mockResolvedValue(undefined) });
  });

  afterEach(() => {
    setStepUpHandlers(null);
  });

  it("opens on 403 step_up_required, asks for the account's factors and replays the request", async () => {
    gated(["password"]);
    render(
      <MemoryRouter>
        <StepUpDialog />
      </MemoryRouter>,
    );
    const user = userEvent.setup();

    const pending = post("/api/guarded", okSchema, { body: {} });

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByLabelText("Authenticator code")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Password"), "password123");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await expect(pending).resolves.toEqual({ status: "ok" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(useAuthStore.getState().refreshSession).toHaveBeenCalled();
  });

  it("shows the code field for TOTP-only accounts", async () => {
    gated(["totp"]);
    render(
      <MemoryRouter>
        <StepUpDialog />
      </MemoryRouter>,
    );

    const pending = post("/api/guarded", okSchema, { body: {} });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(screen.getByText("Authenticator code")).toBeInTheDocument();

    const rejection = expect(pending).rejects.toMatchObject({ code: "step_up_required" });
    await userEvent.setup().click(screen.getByRole("button", { name: "Cancel" }));
    await rejection;
  });
});
