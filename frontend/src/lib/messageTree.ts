import type { MessageResponse } from '@/types';

export interface MessageNode extends MessageResponse {
  childNodes: MessageNode[];
  siblingIndex: number;
  siblingCount: number;
}

export function buildMessageTree(messages: MessageResponse[]): Map<string, MessageNode> {
  const nodeMap = new Map<string, MessageNode>();

  // Create nodes
  for (const msg of messages) {
    nodeMap.set(msg.id, {
      ...msg,
      childNodes: [],
      siblingIndex: 0,
      siblingCount: 1,
    });
  }

  // Link parent → children
  for (const node of nodeMap.values()) {
    if (node.parent_id) {
      const parent = nodeMap.get(node.parent_id);
      if (parent) {
        parent.childNodes.push(node);
      }
    }
  }

  // Set sibling info for children
  for (const node of nodeMap.values()) {
    for (let i = 0; i < node.childNodes.length; i++) {
      node.childNodes[i].siblingIndex = i;
      node.childNodes[i].siblingCount = node.childNodes.length;
    }
  }

  // Set sibling info for root nodes (no parent)
  const roots = Array.from(nodeMap.values()).filter((n) => !n.parent_id);
  for (let i = 0; i < roots.length; i++) {
    roots[i].siblingIndex = i;
    roots[i].siblingCount = roots.length;
  }

  return nodeMap;
}

export function extractBranchPath(
  nodeMap: Map<string, MessageNode>,
  activeBranch: string | null | undefined,
): MessageNode[] {
  if (nodeMap.size === 0) return [];

  // Find root messages (no parent)
  const roots = Array.from(nodeMap.values()).filter((n) => !n.parent_id);
  if (roots.length === 0) return [];

  // If we have an active branch, find it and trace back to root
  if (activeBranch && nodeMap.has(activeBranch)) {
    const targetPath: MessageNode[] = [];
    let node: MessageNode | undefined = nodeMap.get(activeBranch);
    while (node) {
      targetPath.unshift(node);
      node = node.parent_id ? nodeMap.get(node.parent_id) : undefined;
    }
    return targetPath;
  }

  // Default: follow last child from last root
  const path: MessageNode[] = [];
  let current: MessageNode | undefined = roots[roots.length - 1];

  while (current) {
    path.push(current);
    current =
      current.childNodes.length > 0
        ? current.childNodes[current.childNodes.length - 1]
        : undefined;
  }

  return path;
}

export function getBranchChoicesAtMessage(
  nodeMap: Map<string, MessageNode>,
  messageId: string
): { siblings: MessageNode[]; currentIndex: number } {
  const node = nodeMap.get(messageId);
  if (!node) {
    return { siblings: [], currentIndex: 0 };
  }

  // Root-level message: siblings are all root nodes
  if (!node.parent_id) {
    const roots = Array.from(nodeMap.values()).filter((n) => !n.parent_id);
    return {
      siblings: roots,
      currentIndex: node.siblingIndex,
    };
  }

  const parent = nodeMap.get(node.parent_id);
  if (!parent) {
    return { siblings: [], currentIndex: 0 };
  }
  return {
    siblings: parent.childNodes,
    currentIndex: node.siblingIndex,
  };
}
