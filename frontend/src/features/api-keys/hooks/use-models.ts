import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { get } from "@/lib/api-client";

const ModelsResponseSchema = z.object({
  models: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
    }),
  ),
});

export type ModelItem = { id: string; name: string };

function fetchModels() {
  return get("/api/models", ModelsResponseSchema);
}

export function useModels() {
  return useQuery({
    queryKey: ["models"],
    queryFn: fetchModels,
    staleTime: 5 * 60 * 1000,
    select: (data) => data.models,
  });
}
