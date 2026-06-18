import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { createFirewallIp, deleteFirewallIp, listFirewallIps } from "@/features/firewall/api";

export function useFirewall() {
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
      toast.success("IP added to firewall");
      void queryClient.invalidateQueries({ queryKey: ["firewall", "ips"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to add firewall IP");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (ipAddress: string) => deleteFirewallIp(ipAddress),
    onSuccess: () => {
      toast.success("IP removed from firewall");
      void queryClient.invalidateQueries({ queryKey: ["firewall", "ips"] });
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to remove firewall IP");
    },
  });

  return {
    firewallQuery,
    createMutation,
    deleteMutation,
  };
}
