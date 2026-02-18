import { useQuery } from "@tanstack/react-query";

import { listModels } from "@/features/api-keys/api";

export type { ModelItem } from "@/features/api-keys/schemas";

export function useModels() {
  return useQuery({
    queryKey: ["models"],
    queryFn: listModels,
    staleTime: 5 * 60 * 1000,
    select: (data) => data.models,
  });
}
