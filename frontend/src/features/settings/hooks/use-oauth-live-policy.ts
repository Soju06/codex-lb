import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  getOAuthLivePolicy,
  updateOAuthLivePolicy,
} from "@/features/settings/oauth-live-api";
import type { OAuthLivePolicyUpdateRequest } from "@/features/settings/schemas";

const OAUTH_LIVE_POLICY_QUERY_KEY = ["settings", "oauth-live-policy"] as const;

export function useOAuthLivePolicy() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const policyQuery = useQuery({
    queryKey: OAUTH_LIVE_POLICY_QUERY_KEY,
    queryFn: getOAuthLivePolicy,
    staleTime: 30_000,
  });
  const updateMutation = useMutation({
    mutationFn: (payload: OAuthLivePolicyUpdateRequest) => updateOAuthLivePolicy(payload),
    onSuccess: (data) => {
      queryClient.setQueryData(OAUTH_LIVE_POLICY_QUERY_KEY, data);
      toast.success(t("settings.oauthLive.saved"));
    },
    onError: (error: Error) => {
      toast.error(error.message || t("settings.oauthLive.saveFailed"));
    },
  });

  return { policyQuery, updateMutation };
}
