import { get, put } from "@/lib/api-client";

import {
  OAuthLivePolicySchema,
  OAuthLivePolicyUpdateRequestSchema,
} from "@/features/settings/schemas";

const OAUTH_LIVE_POLICY_PATH = "/api/oauth-live-policy";

export function getOAuthLivePolicy() {
  return get(OAUTH_LIVE_POLICY_PATH, OAuthLivePolicySchema);
}

export function updateOAuthLivePolicy(payload: unknown) {
  const validated = OAuthLivePolicyUpdateRequestSchema.parse(payload);
  return put(OAUTH_LIVE_POLICY_PATH, OAuthLivePolicySchema, { body: validated });
}
