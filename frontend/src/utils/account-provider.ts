export function isAnthropicAccountId(accountId: string): boolean {
  return accountId === "anthropic_default" || accountId.startsWith("anthropic_");
}

export function providerLabelForAccountId(accountId: string): "Anthropic" | "Codex" {
  return isAnthropicAccountId(accountId) ? "Anthropic" : "Codex";
}
