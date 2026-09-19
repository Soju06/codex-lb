import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    // `restoreMocks` is not on, and one test replaces `window.location`.
    vi.restoreAllMocks();
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

  it("sends an identity-provider-only account to its provider instead of an empty form", async () => {
    // The account holds no password and no authenticator secret, so there is
    // nothing to type: the two inputs must not appear, and the submit must not
    // be the body endpoint, which would be sent an empty payload and refused.
    gated(["oidc"]);
    const assign = vi.fn();
    vi.spyOn(window, "location", "get").mockReturnValue({
      ...window.location,
      assign,
    } as unknown as Location);
    let started = 0;
    let bodyPosts = 0;
    server.use(
      http.post("/api/dashboard-auth/oidc/step-up/start", () => {
        started += 1;
        return HttpResponse.json({ authorizationUrl: "https://idp.example.test/authorize?x=1" });
      }),
      http.post("/api/dashboard-auth/step-up", () => {
        bodyPosts += 1;
        return HttpResponse.json({ verifiedAt: 1, expiresAt: 301 });
      }),
    );
    render(
      <MemoryRouter>
        <StepUpDialog />
      </MemoryRouter>,
    );

    const pending = post("/api/guarded", okSchema, { body: {} });
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(screen.queryByText("Authenticator code")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();

    const rejection = expect(pending).rejects.toMatchObject({ code: "step_up_required" });
    // Submitting the form (Enter in the dialog) must not fall through to the
    // body endpoint: with no input rendered there is nothing to send, and the
    // server refuses an empty payload.
    const form = document.querySelector("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(bodyPosts).toBe(0);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Continue at your identity provider" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://idp.example.test/authorize?x=1"));
    expect(started).toBe(1);
    expect(bodyPosts).toBe(0);
    // The interrupted request cannot survive a page load, so it is settled as
    // "not verified" rather than left hanging for ever.
    await rejection;
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
