import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  deleteUpstreamProxyGroup,
  getSettings,
  listUpstreamProxyGroups,
  updateSettings,
  upsertUpstreamProxyGroup,
} from "@/features/settings/api";
import type { SettingsUpdateRequest } from "@/features/settings/schemas";

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
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to save settings");
    },
  });

  const proxyGroupsQuery = useQuery({
    queryKey: ["settings", "upstream-proxy-groups"],
    queryFn: listUpstreamProxyGroups,
  });

  const upsertProxyGroupMutation = useMutation({
    mutationFn: ({ name, proxyUrl }: { name: string; proxyUrl: string }) =>
      upsertUpstreamProxyGroup(name, { proxyUrl }),
    onSuccess: () => {
      toast.success("Proxy group saved");
      void queryClient.invalidateQueries({ queryKey: ["settings", "upstream-proxy-groups"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to save proxy group");
    },
  });

  const deleteProxyGroupMutation = useMutation({
    mutationFn: deleteUpstreamProxyGroup,
    onSuccess: () => {
      toast.success("Proxy group deleted");
      void queryClient.invalidateQueries({ queryKey: ["settings", "upstream-proxy-groups"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to delete proxy group");
    },
  });

  return {
    settingsQuery,
    updateSettingsMutation,
    proxyGroupsQuery,
    upsertProxyGroupMutation,
    deleteProxyGroupMutation,
  };
}
