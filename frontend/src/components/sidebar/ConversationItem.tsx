'use client';

import { memo, useState, useRef, useEffect } from 'react';
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
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState(false);
  const removeConversation = useConversationStore((s) => s.removeConversation);
  const menuRef = useRef<HTMLDivElement>(null);

  const handleDelete = async () => {
    try {
      await deleteConversation(conversation.id);
      removeConversation(conversation.id);
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
    setConfirmDelete(false);
    setMenuOpen(false);
  };

  const handleCopyId = async () => {
    try {
      await navigator.clipboard.writeText(conversation.id);
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 1500);
    } catch {
      // fallback: do nothing
    }
    setMenuOpen(false);
  };

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [menuOpen]);

  const title = conversation.title || 'Untitled';
  const date = new Date(conversation.updated_at).toLocaleDateString();

  return (
    <>
      <div
        className={`group relative cursor-pointer transition-colors rounded-lg mx-2 ${
          menuOpen ? 'z-40' : ''
        } ${
          isActive
            ? 'bg-chat dark:bg-panel-accent-dark px-3 py-2.5'
            : 'hover:bg-chat/60 dark:hover:bg-panel-accent-dark/60 px-3 py-2.5'
        }`}
        onClick={() => onSelect(conversation.id)}
        onMouseEnter={() => setShowMenu(true)}
        onMouseLeave={() => { if (!menuOpen) setShowMenu(false); }}
      >
        <div className={`font-medium truncate text-text-primary dark:text-text-primary-dark ${showMenu || menuOpen ? 'pr-7' : ''}`}>
          {title}
        </div>
        <div className="flex items-center gap-2 mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          <span>{date}</span>
          <span>{conversation.message_count} messages</span>
          {copyFeedback && <span className="text-accent">ID copied</span>}
        </div>

        {/* ··· menu trigger */}
        {(showMenu || menuOpen) && (
          <div ref={menuRef} className="absolute right-2 top-1/2 -translate-y-1/2">
            <button
              onClick={(e) => {
                e.stopPropagation();
                setMenuOpen((prev) => !prev);
              }}
              className="p-1.5 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-surface dark:hover:bg-surface-dark transition-colors"
              aria-label="More actions"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                <circle cx="8" cy="3" r="1.5" />
                <circle cx="8" cy="8" r="1.5" />
                <circle cx="8" cy="13" r="1.5" />
              </svg>
            </button>

            {/* Dropdown */}
            {menuOpen && (
              <div className="absolute right-0 top-full mt-1 z-50 w-40 bg-surface dark:bg-panel-dark border border-border dark:border-border-dark rounded-lg shadow-modal p-1">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCopyId();
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 text-sm text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-surface-dark rounded-md transition-colors"
                >
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <rect x="5" y="5" width="9" height="9" rx="1.5" />
                    <path d="M5 11H3.5A1.5 1.5 0 0 1 2 9.5v-7A1.5 1.5 0 0 1 3.5 1h7A1.5 1.5 0 0 1 12 2.5V5" />
                  </svg>
                  复制 ID
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuOpen(false);
                    setConfirmDelete(true);
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 text-sm text-status-error hover:bg-status-error/10 rounded-md transition-colors"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" />
                  </svg>
                  删除对话
                </button>
              </div>
            )}
          </div>
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
