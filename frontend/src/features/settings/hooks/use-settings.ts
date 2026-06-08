import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  addUpstreamProxyPoolMember,
  createUpstreamProxyEndpoint,
  createUpstreamProxyPool,
  getSettings,
  getUpstreamProxyAdmin,
  putAccountProxyBinding,
  updateSettings,
} from "@/features/settings/api";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";
import type {
  AccountProxyBindingRequest,
  UpstreamProxyEndpointCreateRequest,
  UpstreamProxyPoolCreateRequest,
  UpstreamProxyPoolMemberRequest,
} from "@/features/settings/schemas";

export function useSettings() {
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: ["settings", "detail"],
    queryFn: getSettings,
  });

  const updateSettingsMutation = useMutation({
    mutationFn: (payload: SettingsUpdateRequest) => updateSettings(payload),
    onSuccess: () => {
      toast.success("Settings saved");
      void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
      void queryClient.invalidateQueries({ queryKey: ["settings", "upstream-proxy"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to save settings");
    },
  });

  return {
    settingsQuery,
    updateSettingsMutation,
  };
}

export function useUpstreamProxyAdmin() {
  const queryClient = useQueryClient();

  const upstreamProxyQuery = useQuery({
    queryKey: ["settings", "upstream-proxy"],
    queryFn: getUpstreamProxyAdmin,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["settings", "upstream-proxy"] });
    void queryClient.invalidateQueries({ queryKey: ["settings", "detail"] });
  };

  const createEndpointMutation = useMutation({
    mutationFn: (payload: UpstreamProxyEndpointCreateRequest) => createUpstreamProxyEndpoint(payload),
    onSuccess: () => {
      toast.success("Proxy endpoint created");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Proxy endpoint creation failed");
    },
  });

  const createPoolMutation = useMutation({
    mutationFn: (payload: UpstreamProxyPoolCreateRequest) => createUpstreamProxyPool(payload),
    onSuccess: () => {
      toast.success("Proxy pool created");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Proxy pool creation failed");
    },
  });

  const addPoolMemberMutation = useMutation({
    mutationFn: ({ poolId, payload }: { poolId: string; payload: UpstreamProxyPoolMemberRequest }) =>
      addUpstreamProxyPoolMember(poolId, payload),
    onSuccess: () => {
      toast.success("Proxy pool member added");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Proxy pool update failed");
    },
  });

  const accountBindingMutation = useMutation({
    mutationFn: ({ accountId, payload }: { accountId: string; payload: AccountProxyBindingRequest }) =>
      putAccountProxyBinding(accountId, payload),
    onSuccess: () => {
      toast.success("Account proxy binding saved");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Account proxy binding failed");
    },
  });

  return {
    upstreamProxyQuery,
    createEndpointMutation,
    createPoolMutation,
    addPoolMemberMutation,
    accountBindingMutation,
  };
}
