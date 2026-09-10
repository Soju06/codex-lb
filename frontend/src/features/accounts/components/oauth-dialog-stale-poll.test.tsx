import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OauthDialog } from "@/features/accounts/components/oauth-dialog";
import { useOauth } from "@/features/accounts/hooks/use-oauth";

const startOauthMock = vi.fn();
const completeOauthMock = vi.fn();
const submitManualOauthCallbackMock = vi.fn();
const getOauthStatusMock = vi.fn();

vi.mock("@/features/accounts/api", () => ({
  startOauth: (...args: unknown[]) => startOauthMock(...args),
  completeOauth: (...args: unknown[]) => completeOauthMock(...args),
  submitManualOauthCallback: (...args: unknown[]) => submitManualOauthCallbackMock(...args),
  getOauthStatus: (...args: unknown[]) => getOauthStatusMock(...args),
}));

function createDeferred<T>(): {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
} {
  let resolve: ((value: T) => void) | undefined;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  if (resolve === undefined) {
    throw new Error("deferred executor did not run");
  }
  return { promise, resolve };
}

function browserOauthStart(flowId: string) {
  return {
    flowId,
    method: "browser",
    authorizationUrl: `https://auth.example.com/authorize?flow=${flowId}`,
    callbackUrl: "http://127.0.0.1:1455/auth/callback",
    verificationUrl: null,
    userCode: null,
    deviceAuthId: null,
    intervalSeconds: 3600,
    expiresInSeconds: 600,
  };
}

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
}

describe("OauthDialog stale poll generation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getOauthStatusMock.mockResolvedValue({ status: "pending", errorMessage: null });
  });

  it("keeps flow B pending in the dialog when a stale flow A poll succeeds", async () => {
    const statusA = createDeferred<{ status: string; errorMessage: null }>();
    const pollRef: { current: (() => Promise<void>) | null } = { current: null };
    startOauthMock
      .mockResolvedValueOnce(browserOauthStart("flow-a"))
      .mockResolvedValueOnce(browserOauthStart("flow-b"));
    completeOauthMock.mockResolvedValue({ status: "success", errorMessage: null });
    getOauthStatusMock.mockImplementation((flowId: unknown) => {
      if (flowId === "flow-a") {
        return statusA.promise;
      }
      return Promise.resolve({ status: "pending", errorMessage: null });
    });

    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const user = userEvent.setup({ delay: null });

    function Harness() {
      const oauth = useOauth();
      pollRef.current = oauth.poll;
      return (
        <OauthDialog
          open
          state={oauth.state}
          onOpenChange={() => {}}
          onStart={async (method) => {
            await oauth.start(method);
          }}
          onComplete={async () => {}}
          onManualCallback={async (callbackUrl) => {
            await oauth.manualCallback(callbackUrl);
          }}
          onReset={oauth.reset}
        />
      );
    }

    render(createElement(Harness), {
      wrapper: function Wrapper({ children }: PropsWithChildren) {
        return createElement(QueryClientProvider, { client: queryClient }, children);
      },
    });

    await user.click(screen.getByRole("button", { name: "Start sign-in" }));
    expect(await screen.findByText("https://auth.example.com/authorize?flow=flow-a")).toBeInTheDocument();

    const poll = pollRef.current;
    if (poll === null) {
      throw new Error("poll was not exposed");
    }
    let pollA: Promise<void> | undefined;
    act(() => {
      pollA = poll();
    });
    if (pollA === undefined) {
      throw new Error("poll A did not start");
    }
    const pendingPollA = pollA;
    expect(getOauthStatusMock).toHaveBeenCalledWith("flow-a");

    await user.click(screen.getByRole("button", { name: "Change method" }));
    await screen.findByRole("heading", { name: "Add account with OAuth" });
    await user.click(screen.getByRole("button", { name: "Start sign-in" }));
    expect(await screen.findByText("https://auth.example.com/authorize?flow=flow-b")).toBeInTheDocument();

    await act(async () => {
      statusA.resolve({ status: "success", errorMessage: null });
      await pendingPollA;
    });

    await waitFor(() => {
      expect(screen.getByText("https://auth.example.com/authorize?flow=flow-b")).toBeInTheDocument();
    });
    expect(screen.getByText("Waiting for authorization to complete...")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Account added" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Authorization failed" })).not.toBeInTheDocument();
    expect(completeOauthMock).not.toHaveBeenCalled();
    expect(invalidateSpy).not.toHaveBeenCalled();
  });
});
