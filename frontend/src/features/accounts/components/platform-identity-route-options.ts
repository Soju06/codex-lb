import type { PlatformRouteFamily } from "@/features/accounts/schemas";

export type CheckedState = boolean | "indeterminate";

export const PLATFORM_ROUTE_FAMILY_ORDER: PlatformRouteFamily[] = [
  "public_models_http",
  "public_responses_http",
  "backend_codex_http",
];

export const PLATFORM_ROUTE_OPTIONS: Array<{
  value: PlatformRouteFamily;
  label: string;
  description: string;
}> = [
  {
    value: "public_models_http",
    label: "/v1/models",
    description: "Allow this identity to back public model discovery.",
  },
  {
    value: "public_responses_http",
    label: "/v1/responses",
    description: "Allow this identity to handle stateless HTTP Responses API calls only.",
  },
  {
    value: "backend_codex_http",
    label: "/backend-api/codex HTTP",
    description: "Allow this identity to back Codex HTTP models and stateless HTTP responses only.",
  },
];

export function shouldIncludeRouteFamily(checked: CheckedState): boolean {
  return checked === true;
}
