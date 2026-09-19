import type { AccountProvider } from "@/features/accounts/schemas";

const PROVIDER_LABELS: Record<AccountProvider, string> = {
  openai: "Codex",
  anthropic: "Claude",
  glm: "GLM",
  kimi: "Kimi",
  openrouter: "OpenRouter",
};

export function providerLabel(provider: AccountProvider | undefined): string {
  return PROVIDER_LABELS[provider ?? "openai"];
}
