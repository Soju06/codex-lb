import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { createFirewallIp, deleteFirewallIp, listFirewallIps } from "@/features/firewall/api";

export function useFirewall() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const { data, error, isFetching, isLoading, isPending, isSuccess, refetch } = useQuery({
    queryKey: ["firewall", "ips"],
    queryFn: listFirewallIps,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
  const firewallQuery = { data, error, isFetching, isLoading, isPending, isSuccess, refetch };

  const createMutation = useMutation({
    mutationFn: (ipAddress: string) => createFirewallIp({ ipAddress }),
    onSuccess: () => {
      toast.success(t("firewall.toasts.added"));
      void queryClient.invalidateQueries({ queryKey: ["firewall", "ips"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("firewall.toasts.addFailed"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (ipAddress: string) => deleteFirewallIp(ipAddress),
    onSuccess: () => {
      toast.success(t("firewall.toasts.removed"));
      void queryClient.invalidateQueries({ queryKey: ["firewall", "ips"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("firewall.toasts.removeFailed"));
    },
  });

  return {
    firewallQuery,
    createMutation,
    deleteMutation,
  };
}
