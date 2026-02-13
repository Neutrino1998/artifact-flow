'use client';

import { memo, useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { getBranchChoicesAtMessage } from '@/lib/messageTree';

interface BranchNavigatorProps {
  messageId: string;
  currentIndex: number;
  totalSiblings: number;
}

function BranchNavigator({
  messageId,
  currentIndex,
  totalSiblings,
}: BranchNavigatorProps) {
  const nodeMap = useConversationStore((s) => s.nodeMap);
  const setActiveBranch = useConversationStore((s) => s.setActiveBranch);

  const navigate = useCallback(
    (direction: -1 | 1) => {
      const { siblings } = getBranchChoicesAtMessage(nodeMap, messageId);
      const newIndex = currentIndex + direction;
      if (newIndex >= 0 && newIndex < siblings.length) {
        setActiveBranch(siblings[newIndex].id);
      }
    },
    [nodeMap, messageId, currentIndex, setActiveBranch]
  );

  return (
    <div className="flex items-center justify-center gap-1.5 text-xs text-text-tertiary dark:text-text-tertiary-dark">
      <button
        onClick={() => navigate(-1)}
        disabled={currentIndex === 0}
        className="p-0.5 rounded hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        aria-label="Previous branch"
        title="上一个分支"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M7.5 2.5 4 6l3.5 3.5" />
        </svg>
      </button>
      <span>
        {currentIndex + 1}/{totalSiblings}
      </span>
      <button
        onClick={() => navigate(1)}
        disabled={currentIndex === totalSiblings - 1}
        className="p-0.5 rounded hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        aria-label="Next branch"
        title="下一个分支"
      >
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M4.5 2.5 8 6l-3.5 3.5" />
        </svg>
      </button>
    </div>
  );
}

export default memo(BranchNavigator);
