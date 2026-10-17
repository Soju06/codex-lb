// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ThreadIdentityFacet, ThreadIdentityResponse } from "../schemas";
import { ThreadIdentityCard } from "./thread-identity-card";

function facet(overrides: Partial<ThreadIdentityFacet> = {}): ThreadIdentityFacet {
  return {
    requests: 0,
    requestShare: 0,
    unattributedRequestShare: 0,
    conversations: 0,
    meanAccountsPerConversation: 0,
    singleAccountConversationShare: 0,
    turns: 0,
    accountSwitchRate: 0,
    cacheHitRatio: 0,
    cacheSampleInputTokens: 0,
    threadGroupingApproximate: false,
    ...overrides,
  };
}

function response(overrides: Partial<ThreadIdentityResponse> = {}): ThreadIdentityResponse {
  return {
    available: true,
    maxDays: 7,
    windowDays: 1,
    conversationMinRequests: 3,
    switchMaxGapSeconds: 600,
    cacheMinInputTokens: 5000,
    totalRequests: 0,
    unkeyedRequestShare: 0,
    keyed: facet(),
    unkeyed: facet({ threadGroupingApproximate: true }),
    ...overrides,
  };
}

describe("ThreadIdentityCard", () => {
  it("renders the 09-09 baseline figures in the keyed column", () => {
    render(
      <ThreadIdentityCard
        data={response({
          totalRequests: 99_675,
          unkeyedRequestShare: 0.3293,
          keyed: facet({
            requests: 66_853,
            requestShare: 0.6707,
            unattributedRequestShare: 0.096,
            conversations: 1929,
            meanAccountsPerConversation: 2.2918,
            singleAccountConversationShare: 0.4526,
            turns: 57_950,
            accountSwitchRate: 0.078,
            cacheHitRatio: 0.9098,
            cacheSampleInputTokens: 7_365_036_635,
          }),
        })}
      />,
    );

    expect(screen.getByTestId("thread-identity-keyed-factor")).toHaveTextContent("2.29");
    expect(screen.getByTestId("thread-identity-keyed-factor")).toHaveTextContent("1,929 conversations");
    expect(screen.getByTestId("thread-identity-keyed-single-account")).toHaveTextContent("45.3%");
    expect(screen.getByTestId("thread-identity-keyed-switch-rate")).toHaveTextContent("7.8%");
    expect(screen.getByTestId("thread-identity-keyed-cache-hit")).toHaveTextContent("91.0%");
  });

  it("labels the unkeyed thread grouping as a reconstruction", () => {
    render(<ThreadIdentityCard data={response()} />);

    expect(
      screen.getByText(/reconstructed by API key and are approximate/i),
    ).toBeInTheDocument();
  });

  it("renders a sub-tenth-of-a-percent cache ratio without rounding it to zero", () => {
    render(
      <ThreadIdentityCard data={response({ unkeyed: facet({ cacheHitRatio: 0.0005, threadGroupingApproximate: true }) })} />,
    );

    expect(screen.getByTestId("thread-identity-unkeyed-cache-hit")).toHaveTextContent("<0.1%");
  });

  it("discloses detached account attribution that drags the factor down", () => {
    render(
      <ThreadIdentityCard
        data={response({
          totalRequests: 100,
          keyed: facet({ requests: 80, unattributedRequestShare: 0.25 }),
          unkeyed: facet({ requests: 20, threadGroupingApproximate: true }),
        })}
      />,
    );

    // 20 of 100 requests lost their account when the account was deleted.
    expect(screen.getByText(/^20\.0% of requests in this window/)).toBeInTheDocument();
  });

  it("omits the attribution note when every request still has an account", () => {
    render(<ThreadIdentityCard data={response({ totalRequests: 100, keyed: facet({ requests: 100 }) })} />);

    expect(screen.queryByText(/no account attribution/)).not.toBeInTheDocument();
  });

  it("explains that a wide range is not measured instead of showing zeros", () => {
    render(<ThreadIdentityCard data={response({ available: false, windowDays: 30 })} />);

    expect(screen.getByText(/7 days or less/i)).toBeInTheDocument();
    expect(screen.queryByTestId("thread-identity-keyed-factor")).not.toBeInTheDocument();
  });
});
