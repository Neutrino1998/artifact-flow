// Compact duration formatter for execution summaries. Keep this shared between
// the user flow header and admin observability so the same turn reads the same.
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) {
    const tenths = Math.floor(ms / 100) / 10;
    return `${tenths.toFixed(1)}s`;
  }
  const minutes = Math.floor(totalSec / 60);
  const remainingSeconds = totalSec - minutes * 60;
  return `${minutes}m ${remainingSeconds}s`;
}
