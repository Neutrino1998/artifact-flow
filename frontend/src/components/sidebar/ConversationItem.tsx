'use client';

import { memo, useState } from 'react';
import type { ConversationSummary } from '@/types';
import { deleteConversation } from '@/lib/api';
import { useConversationStore } from '@/stores/conversationStore';

interface ConversationItemProps {
  conversation: ConversationSummary;
  isActive: boolean;
  onSelect: (id: string) => void;
}

function ConversationItem({ conversation, isActive, onSelect }: ConversationItemProps) {
  const [showMenu, setShowMenu] = useState(false);
  const removeConversation = useConversationStore((s) => s.removeConversation);

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteConversation(conversation.id);
      removeConversation(conversation.id);
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
    setShowMenu(false);
  };

  const title = conversation.title || 'Untitled';
  const date = new Date(conversation.updated_at).toLocaleDateString();

  return (
    <div
      className={`group relative px-3 py-2.5 cursor-pointer transition-colors ${
        isActive
          ? 'bg-accent/10 border-r-2 border-accent'
          : 'hover:bg-bg dark:hover:bg-bg-dark'
      }`}
      onClick={() => onSelect(conversation.id)}
      onMouseEnter={() => setShowMenu(true)}
      onMouseLeave={() => setShowMenu(false)}
    >
      <div className="text-sm truncate text-text-primary dark:text-text-primary-dark">
        {title}
      </div>
      <div className="flex items-center gap-2 mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark">
        <span>{date}</span>
        <span>{conversation.message_count} msgs</span>
      </div>

      {/* Hover menu */}
      {showMenu && (
        <button
          onClick={handleDelete}
          className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error hover:bg-status-error/10 transition-colors"
          aria-label="Delete conversation"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M2 3.5h10M5.5 3.5V2.5a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1M9 6v4.5M5 6v4.5M3.5 3.5l.5 8a1 1 0 0 0 1 1h4a1 1 0 0 0 1-1l.5-8" />
          </svg>
        </button>
      )}
    </div>
  );
}

export default memo(ConversationItem);
