import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { getCacheProbePlan, runCacheProbe } from "@/features/cache-probe/api";
import type { CacheProbeRun } from "@/features/cache-probe/schemas";

export const CACHE_PROBE_QUERY_KEY = ["cache-isolation-probe", "plan"] as const;

const DEFAULT_SEED_REPETITIONS = 3;
const DEFAULT_OTHER_ACCOUNT_COUNT = 4;

export function useCacheIsolationProbe(enabled: boolean) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [seedRepetitions, setSeedRepetitions] = useState(DEFAULT_SEED_REPETITIONS);
  const [otherAccountCount, setOtherAccountCount] = useState(DEFAULT_OTHER_ACCOUNT_COUNT);
  const [result, setResult] = useState<CacheProbeRun | null>(null);

  const planQuery = useQuery({
    queryKey: CACHE_PROBE_QUERY_KEY,
    queryFn: getCacheProbePlan,
    enabled,
    // The plan is only a cost preview and a pool verdict; it is re-read when
    // the operator opens the card and after a run, never on a timer, because
    // polling it would add load to a pool the card exists to protect.
    refetchOnWindowFocus: false,
  });

  const runMutation = useMutation({
    mutationFn: () =>
      runCacheProbe({ confirm: true as const, seedRepetitions, otherAccountCount }),
    onSuccess: async (run: CacheProbeRun) => {
      setResult(run);
      if (run.crossAccountHit) {
        toast.warning(t("cacheProbe.toasts.crossAccountHit"));
      } else {
        toast.success(t("cacheProbe.toasts.completed"));
      }
      await queryClient.invalidateQueries({ queryKey: CACHE_PROBE_QUERY_KEY });
    },
    onError: (error: Error) => {
      toast.error(error.message || t("cacheProbe.toasts.failed"));
    },
  });

  return {
    planQuery,
    runMutation,
    result,
    seedRepetitions,
    otherAccountCount,
    setSeedRepetitions,
    setOtherAccountCount,
  };
}
