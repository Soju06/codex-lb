import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { createFirewallIp, deleteFirewallIp, listFirewallIps } from "@/features/firewall/api";

export function useFirewall() {
  const queryClient = useQueryClient();

  const firewallQuery = useQuery({
    queryKey: ["firewall", "ips"],
    queryFn: listFirewallIps,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["firewall", "ips"] });
  };

  const createMutation = useMutation({
    mutationFn: (ipAddress: string) => createFirewallIp({ ipAddress }),
    onSuccess: () => {
      toast.success("IP added to firewall");
      invalidate();
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to add firewall IP");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (ipAddress: string) => deleteFirewallIp(ipAddress),
    onSuccess: () => {
      toast.success("IP removed from firewall");
      invalidate();
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
