import { cn } from "@/lib/utils";
import type { AccountProvider } from "@/features/accounts/schemas";

const PROVIDER_ICON_URLS: Record<AccountProvider, string> = {
  openai: "/brand/openai.svg",
  anthropic: "/brand/claude.svg",
  glm: "/brand/openai.svg",
  kimi: "/brand/openai.svg",
  openrouter: "/brand/openai.svg",
};

export function ProviderIcon({
  provider,
  className,
}: {
  provider?: AccountProvider;
  className?: string;
}) {
  const normalizedProvider = provider ?? "openai";

  return (
    <span
      aria-hidden="true"
      className={cn("inline-block size-3.5 shrink-0 bg-current", className)}
      style={{
        WebkitMaskImage: `url(${PROVIDER_ICON_URLS[normalizedProvider]})`,
        maskImage: `url(${PROVIDER_ICON_URLS[normalizedProvider]})`,
        WebkitMaskRepeat: "no-repeat",
        maskRepeat: "no-repeat",
        WebkitMaskPosition: "center",
        maskPosition: "center",
        WebkitMaskSize: "contain",
        maskSize: "contain",
      }}
    />
  );
}
