'use client';

import { useEffect, useState, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { fetchNotifications, dismissNotification, type Notification, type Severity } from '@/lib/siteConfig';

interface Props {
  collapsed?: boolean;
}

// 60s 是 starts_at/ends_at 过期 / 生效的最大延迟，也是运维改 JSON 到生效的最大延迟。
// 比这更频繁意义不大（通知配置低频变动），更稀疏会让时间窗语义失真。
const POLL_INTERVAL_MS = 60_000;

const SEVERITY_DOT_CLASS: Record<Severity, string> = {
  info: 'bg-accent',
  warn: 'bg-status-warning',
  critical: 'bg-status-error',
};

const SEVERITY_TEXT_CLASS: Record<Severity, string> = {
  info: 'text-accent',
  warn: 'text-status-warning',
  critical: 'text-status-error',
};

const SEVERITY_BG_TINT: Record<Severity, string> = {
  info: 'bg-accent/10',
  warn: 'bg-status-warning/10',
  critical: 'bg-status-error/10',
};

function BellIcon({ className = '' }: { className?: string }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className={className}>
      <path d="M8 2v1M4 6a4 4 0 0 1 8 0v3l1.5 2H2.5L4 9V6z" strokeLinejoin="round" />
      <path d="M6.5 13a1.5 1.5 0 0 0 3 0" />
    </svg>
  );
}

export default function NotificationCenter({ collapsed }: Props) {
  const [items, setItems] = useState<Notification[]>([]);
  const [open, setOpen] = useState(false);

  const reload = useCallback(() => {
    void fetchNotifications().then(setItems);
  }, []);

  // items 清空时强制关 modal——否则用户开着 modal 时 poll 把通知刷没了，
  // open 状态会残留，下次 poll 通知再回来时 modal 会"自己弹出"。
  useEffect(() => {
    if (items.length === 0) setOpen(false);
  }, [items.length]);

  useEffect(() => {
    reload();
    const timer = window.setInterval(reload, POLL_INTERVAL_MS);

    // 标签从隐藏切回可见时立即重拉一次 —— 覆盖"开着标签去开会两小时回来"
    // 这种 setInterval 节流被浏览器降频的场景。
    const onVisibility = () => {
      if (document.visibilityState === 'visible') reload();
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [reload]);

  if (items.length === 0) return null;

  const top = items[0];
  const extra = items.length - 1;

  const handleDismiss = (id: string) => {
    dismissNotification(id);
    setItems((prev) => prev.filter((n) => n.id !== id));
  };

  // Collapsed: bell icon button with severity dot
  if (collapsed) {
    return (
      <>
        <button
          onClick={() => setOpen(true)}
          className="relative w-10 h-10 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-chat/60 dark:hover:bg-panel-accent-dark/60 transition-colors"
          title={`${items.length} 条通知`}
          aria-label="查看通知"
        >
          <BellIcon />
          <span className={`absolute top-1.5 right-1.5 w-2 h-2 rounded-full ring-2 ring-panel-accent dark:ring-panel-dark ${SEVERITY_DOT_CLASS[top.severity]}`} />
        </button>
        {open && <NotificationModal items={items} onClose={() => setOpen(false)} onDismiss={handleDismiss} />}
      </>
    );
  }

  // Expanded: card matching UserMenu style
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="w-full flex items-center gap-3 px-3 py-2.5 bg-chat dark:bg-panel-accent-dark rounded-card hover:bg-surface dark:hover:bg-[#141414] transition-colors text-left"
      >
        <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${SEVERITY_BG_TINT[top.severity]}`}>
          <BellIcon className={SEVERITY_TEXT_CLASS[top.severity]} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-medium text-text-primary dark:text-text-primary-dark truncate flex items-center gap-1.5">
            <span className="truncate">{top.title}</span>
            {extra > 0 && (
              <span className="inline-block px-1 py-px text-[10px] rounded bg-accent/10 text-accent shrink-0">
                +{extra}
              </span>
            )}
          </div>
          <div className="text-xs text-text-secondary dark:text-text-secondary-dark truncate">
            点击查看详情
          </div>
        </div>
      </button>
      {open && <NotificationModal items={items} onClose={() => setOpen(false)} onDismiss={handleDismiss} />}
    </>
  );
}

interface ModalProps {
  items: Notification[];
  onClose: () => void;
  onDismiss: (id: string) => void;
}

function NotificationModal({ items, onClose, onDismiss }: ModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={onClose}>
      <div
        className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-card shadow-modal max-w-xl w-full mx-4 max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-border dark:border-border-dark">
          <h2 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark">
            通知 ({items.length})
          </h2>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            aria-label="关闭"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {items.map((n) => (
            <div
              key={n.id}
              className={`rounded-card p-4 ${SEVERITY_BG_TINT[n.severity]} border border-border/40 dark:border-border-dark/40`}
            >
              <div className="flex items-start justify-between gap-3 mb-2">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full ${SEVERITY_DOT_CLASS[n.severity]}`} />
                  <h3 className="font-semibold text-text-primary dark:text-text-primary-dark">
                    {n.title}
                  </h3>
                </div>
                {(n.dismissible ?? true) && (
                  <button
                    onClick={() => onDismiss(n.id)}
                    className="text-xs text-text-tertiary dark:text-text-tertiary-dark hover:text-text-primary dark:hover:text-text-primary-dark transition-colors shrink-0"
                  >
                    不再提示
                  </button>
                )}
              </div>
              <div className="prose prose-sm dark:prose-invert max-w-none text-text-secondary dark:text-text-secondary-dark">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{n.body}</ReactMarkdown>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
