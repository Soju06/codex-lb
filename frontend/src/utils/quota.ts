export function quotaBreakdown(percent: number, cap: number | null | undefined) {
  const provider = Math.max(0, Math.min(100, percent));
  const reserved = cap == null ? 0 : Math.min(provider, 100 - cap);
  return { provider, reserved, usable: Math.max(0, provider - reserved) };
}

export function complementPercent(value: number): number {
  const [coefficient, exponent = "0"] = String(value).toLowerCase().split("e");
  const decimals = Math.max(0, (coefficient.split(".")[1]?.length ?? 0) - Number(exponent));
  return Number((100 - value).toFixed(Math.min(decimals, 20)));
}
