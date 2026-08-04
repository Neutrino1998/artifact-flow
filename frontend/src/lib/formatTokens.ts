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

type DisplayTokenUsage = {
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens?: number | null;
};

// Cache reads are a subset of input tokens, so keep them parenthesized beside
// input instead of presenting the three values as additive peers.
export function formatTokenUsage(usage: DisplayTokenUsage): string {
  const cached = usage.cached_input_tokens != null
    ? ` (${formatTokens(usage.cached_input_tokens)} ↻)`
    : '';
  return `${formatTokens(usage.input_tokens)} ↑${cached} · ${formatTokens(usage.output_tokens)} ↓`;
}
