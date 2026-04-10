'use client';

import { memo, useState } from 'react';
import type { ConversationSummary } from '@/types';
import { deleteConversation } from '@/lib/api';
import { useConversationStore } from '@/stores/conversationStore';
import ConfirmModal from '@/components/layout/ConfirmModal';

interface ConversationItemProps {
  conversation: ConversationSummary;
  isActive: boolean;
  onSelect: (id: string) => void;
}

function ConversationItem({ conversation, isActive, onSelect }: ConversationItemProps) {
  const [showMenu, setShowMenu] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const removeConversation = useConversationStore((s) => s.removeConversation);

  const handleDelete = async () => {
    try {
      await deleteConversation(conversation.id);
      removeConversation(conversation.id);
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
    setConfirmDelete(false);
    setShowMenu(false);
  };

  const title = conversation.title || 'Untitled';
  const date = new Date(conversation.updated_at).toLocaleDateString();

  return (
    <>
      <div
        className={`group relative cursor-pointer transition-colors rounded-lg mx-2 ${
          isActive
            ? 'bg-chat dark:bg-panel-accent-dark px-3 py-2.5'
            : 'hover:bg-chat/60 dark:hover:bg-panel-accent-dark/60 px-3 py-2.5'
        }`}
        onClick={() => onSelect(conversation.id)}
        onMouseEnter={() => setShowMenu(true)}
        onMouseLeave={() => setShowMenu(false)}
      >
        <div className={`font-medium truncate text-text-primary dark:text-text-primary-dark ${showMenu ? 'pr-7' : ''}`}>
          {title}
        </div>
        <div className="flex items-center gap-2 mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          <span>{date}</span>
          <span>{conversation.message_count} messages</span>
        </div>

        {/* Hover delete */}
        {showMenu && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setConfirmDelete(true);
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error dark:hover:text-status-error hover:bg-status-error/10 dark:hover:bg-status-error/10 transition-colors"
            aria-label="Delete conversation"
            title="删除对话"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" />
            </svg>
          </button>
        )}
      </div>

      {confirmDelete && (
        <ConfirmModal
          title="删除对话"
          message={`确定要删除对话「${title}」吗？此操作无法撤销。`}
          confirmLabel="删除"
          destructive
          onConfirm={handleDelete}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </>
  );
}

export default memo(ConversationItem);
