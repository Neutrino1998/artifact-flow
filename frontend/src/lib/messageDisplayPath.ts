import type { MessageNode } from './messageTree';

export function resolveStreamingDisplayPath(
  branchPath: MessageNode[],
  isStreamingHere: boolean,
  streamParentId: string | null | undefined,
): MessageNode[] {
  if (!isStreamingHere || streamParentId === undefined) return branchPath;
  if (streamParentId === null) return [];
  const idx = branchPath.findIndex((n) => n.id === streamParentId);
  if (idx === -1) return branchPath;
  return branchPath.slice(0, idx + 1);
}
