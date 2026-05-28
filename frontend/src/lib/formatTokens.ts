// Compact token-count formatter for the dense chat chrome (ProcessingFlow
// header, composer context gauge). 0–999 → exact; <1M → "45.2K" / "120K";
// ≥1M → "1.2M". Keeps one decimal only where it adds signal (<10K, <10M).
export function formatTokens(n: number): string {
  if (n < 1000) return `${n}`;
  if (n < 1_000_000) {
    const k = n / 1000;
    return `${k < 10 ? k.toFixed(1) : Math.round(k)}K`;
  }
  const m = n / 1_000_000;
  return `${m < 10 ? m.toFixed(1) : Math.round(m)}M`;
}
