// Compact human-readable byte formatter for upload-progress chrome.
// 1024-based units (matches the OS file size users see). Keeps one decimal
// only where it adds signal (<10 units in the chosen scale).
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) {
    const k = n / 1024;
    return `${k < 10 ? k.toFixed(1) : Math.round(k)} KB`;
  }
  if (n < 1024 * 1024 * 1024) {
    const m = n / (1024 * 1024);
    return `${m < 10 ? m.toFixed(1) : Math.round(m)} MB`;
  }
  const g = n / (1024 * 1024 * 1024);
  return `${g < 10 ? g.toFixed(1) : Math.round(g)} GB`;
}
