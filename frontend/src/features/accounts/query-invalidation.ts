import type { QueryClient } from "@tanstack/react-query";

export async function invalidateAccountRelatedQueries(queryClient: QueryClient, accountId?: string) {
  const invalidations = [
    queryClient.invalidateQueries({ queryKey: ["accounts", "list"] }),
    queryClient.invalidateQueries({ queryKey: ["accounts", "trends"] }),
    queryClient.invalidateQueries({ queryKey: ["accounts", "usage-reset-credits"] }),
    queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] }),
    queryClient.invalidateQueries({ queryKey: ["dashboard", "projections"] }),
    queryClient.invalidateQueries({ queryKey: ["usage"] }),
  ];
  if (accountId) {
    invalidations.push(queryClient.invalidateQueries({ queryKey: ["accounts", "trends", accountId] }));
  }
  await Promise.all(invalidations);
}
