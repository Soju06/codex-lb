import type { LimitRuleCreate } from "@/features/api-keys/schemas";

type NormalizedLimitRule = {
  limitType: LimitRuleCreate["limitType"];
  limitWindow: LimitRuleCreate["limitWindow"];
  maxValue: number;
  modelFilter: string | null;
};

function normalizeLimitRule(rule: LimitRuleCreate): NormalizedLimitRule {
  const rawModelFilter = typeof rule.modelFilter === "string" ? rule.modelFilter.trim() : null;
  return {
    limitType: rule.limitType,
    limitWindow: rule.limitWindow,
    maxValue: rule.maxValue,
    modelFilter: rawModelFilter || null,
  };
}

function limitRuleSortKey(rule: NormalizedLimitRule): string {
  return [
    rule.limitType,
    rule.limitWindow,
    rule.modelFilter ?? "",
    String(rule.maxValue),
  ].join("::");
}

export function normalizeLimitRules(rules: LimitRuleCreate[]): NormalizedLimitRule[] {
  return rules
    .filter((rule) => rule.maxValue > 0)
    .map(normalizeLimitRule)
    .sort((left, right) => limitRuleSortKey(left).localeCompare(limitRuleSortKey(right)));
}

export function hasLimitRuleChanges(
  initialRules: LimitRuleCreate[],
  currentRules: LimitRuleCreate[],
): boolean {
  const initial = normalizeLimitRules(initialRules);
  const current = normalizeLimitRules(currentRules);
  if (initial.length !== current.length) {
    return true;
  }
  return initial.some((rule, index) => {
    const candidate = current[index];
    if (!candidate) {
      return true;
    }
    return (
      rule.limitType !== candidate.limitType
      || rule.limitWindow !== candidate.limitWindow
      || rule.maxValue !== candidate.maxValue
      || rule.modelFilter !== candidate.modelFilter
    );
  });
}
