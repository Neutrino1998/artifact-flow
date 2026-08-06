import type { VersionSummary } from '@/types';

/**
 * Find the persisted version immediately before `currentVersion`.
 *
 * Version numbers are intentionally sparse because multiple edits in one turn
 * fold into one durable snapshot, so arithmetic (`currentVersion - 1`) is not
 * reliable.
 */
export function findPrevVersion(
  versions: VersionSummary[],
  currentVersion: number,
): number | null {
  const sorted = versions.map((version) => version.version).sort((a, b) => a - b);
  const index = sorted.indexOf(currentVersion);
  return index > 0 ? sorted[index - 1] : null;
}
