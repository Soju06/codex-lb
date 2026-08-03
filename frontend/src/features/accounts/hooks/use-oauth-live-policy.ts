import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  getOAuthLivePolicy,
  updateOAuthLivePolicy,
} from "@/features/accounts/api";
import type { OAuthLivePolicyUpdateRequest } from "@/features/accounts/schemas";

export function useOAuthLivePolicy(accountId: string | null) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const queryKey = ["accounts", "oauth-live-policy", accountId] as const;
  const policyQuery = useQuery({
    queryKey,
    queryFn: () => getOAuthLivePolicy(accountId as string),
    enabled: !!accountId,
    staleTime: 30_000,
  });
  const updateMutation = useMutation({
    mutationFn: (payload: OAuthLivePolicyUpdateRequest) =>
      updateOAuthLivePolicy(accountId as string, payload),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKey, data);
      toast.success(t("accounts.oauthLive.saved"));
    },
    onError: (error: Error) => {
      toast.error(error.message || t("accounts.oauthLive.saveFailed"));
    },
  });

  return { policyQuery, updateMutation };
}
