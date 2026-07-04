'use client';

import { memo, useState } from 'react';
import type { ConversationSummary } from '@/types';
import { deleteConversation } from '@/lib/api';
import { useConversationStore } from '@/stores/conversationStore';
import { parseUtcIso } from '@/lib/time';
import ConversationActionsMenu from './ConversationActionsMenu';

interface ConversationItemProps {
  conversation: ConversationSummary;
  isActive: boolean;
  onSelect: (id: string) => void;
}

function ConversationItem({ conversation, isActive, onSelect }: ConversationItemProps) {
  const [showMenu, setShowMenu] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const removeConversation = useConversationStore((s) => s.removeConversation);

  // 侧栏自持删除:调 API + 本地 store 摘除(浏览器那侧是委派父级刷新,故删除动作注入)。
  const handleDelete = async (id: string) => {
    try {
      await deleteConversation(id);
      removeConversation(id);
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  };

  const title = conversation.title || 'Untitled';
  const date = parseUtcIso(conversation.updated_at).toLocaleDateString();

  return (
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
      <div className={`flex items-center gap-1.5 font-medium text-text-primary dark:text-text-primary-dark ${showMenu || menuOpen ? 'pr-7' : ''}`}>
        {conversation.active_message_id && (
          <span
            className="inline-block w-2 h-2 rounded-full bg-status-running flex-shrink-0"
            title="运行中"
          />
        )}
        <span className="truncate">{title}</span>
      </div>
      <div className="flex items-center gap-2 mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark">
        <span>{date}</span>
        <span>{conversation.message_count} messages</span>
      </div>

      <ConversationActionsMenu
        conversationId={conversation.id}
        title={title}
        visible={showMenu || menuOpen}
        open={menuOpen}
        onOpenChange={setMenuOpen}
        onDelete={handleDelete}
        wrapperClassName="absolute right-2 top-1/2 -translate-y-1/2"
      />
    </div>
  );
}

export default memo(ConversationItem);
