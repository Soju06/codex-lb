import type { QueryClient } from "@tanstack/react-query";

export function invalidateAccountRelatedQueries(queryClient: QueryClient, accountId?: string) {
  void queryClient.invalidateQueries({ queryKey: ["accounts", "list"] });
  void queryClient.invalidateQueries({ queryKey: ["accounts", "trends"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] });
  void queryClient.invalidateQueries({ queryKey: ["dashboard", "projections"] });
  if (accountId) {
    void queryClient.invalidateQueries({ queryKey: ["accounts", "trends", accountId] });
  }
}
