import { describe, expect, it } from "vitest";

import {
  isAnthropicAccountId,
  providerLabelForAccountId,
} from "@/utils/account-provider";

describe("account provider helpers", () => {
  it("detects Anthropic account ids", () => {
    expect(isAnthropicAccountId("anthropic_default")).toBe(true);
    expect(isAnthropicAccountId("anthropic_user_1")).toBe(true);
    expect(isAnthropicAccountId("codex_primary")).toBe(false);
  });

  it("maps account ids to provider labels", () => {
    expect(providerLabelForAccountId("anthropic_default")).toBe("Anthropic");
    expect(providerLabelForAccountId("anthropic_team")).toBe("Anthropic");
    expect(providerLabelForAccountId("codex_primary")).toBe("Codex");
  });
});
