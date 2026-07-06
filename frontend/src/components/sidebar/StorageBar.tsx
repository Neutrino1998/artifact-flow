'use client';

import { useEffect, useState } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { getStorageUsage } from '@/lib/api';
import { formatBytes } from '@/lib/formatBytes';
import type { StorageUsageResponse } from '@/types';

// Per-user storage indicator shown in the user menu.
//
// The displayed used/quota always comes from the authoritative GET /chat/storage
// (artifact blobs + private skill bundles) — never from the paginated sidebar
// list, which only holds the first page. We subscribe to the conversation list
// purely as a *change signal* for artifact changes: deleting a conversation
// (removeConversation drops total + bytes) and an upload completing (COMPLETE
// refreshes the list with new upload_bytes) both shift [total, bytesSum], which
// re-pulls the real total.
export default function StorageBar() {
  const total = useConversationStore((s) => s.total);
  const bytesSum = useConversationStore((s) =>
    s.conversations.reduce((acc, c) => acc + (c.upload_bytes || 0), 0)
  );

  const [usage, setUsage] = useState<StorageUsageResponse | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [displayPct, setDisplayPct] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoadFailed(false);
    getStorageUsage()
      .then((u) => {
        if (!cancelled) setUsage(u);
      })
      .catch(() => {
        // Non-critical chrome — keep a stable placeholder instead of shifting
        // the user menu while the request fails.
        if (!cancelled) {
          setUsage(null);
          setLoadFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [total, bytesSum]);

  const { used_bytes, quota_bytes } = usage || { used_bytes: 0, quota_bytes: 0 };
  const unlimited = quota_bytes <= 0;
  const pct = unlimited
    ? 0
    : Math.min(100, Math.round((used_bytes / quota_bytes) * 100));
  const near = !unlimited && pct >= 90;
  const valueText = usage
    ? unlimited
      ? `${formatBytes(used_bytes)} / 不限额`
      : `${formatBytes(used_bytes)} / ${formatBytes(quota_bytes)}`
    : loadFailed
      ? '暂不可用'
      : '读取中...';
  const tooltipText = `存储空间统计上传文件、二进制产物和私有技能包占用的总大小，共用同一个存储配额。当前：${valueText}`;

  useEffect(() => {
    if (!usage || unlimited) {
      setDisplayPct(0);
      return;
    }

    setDisplayPct(0);
    const frame = window.requestAnimationFrame(() => {
      setDisplayPct(pct);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [pct, usage, unlimited]);

  return (
    <div
      className="relative h-5 overflow-hidden rounded-lg bg-white/50 dark:bg-black/25"
      aria-label={tooltipText}
      title={tooltipText}
    >
      {!unlimited && (
        <div
          className={`absolute inset-y-0 left-0 rounded-lg transition-[width] duration-500 ease-out ${
            near ? 'bg-status-error/20' : 'bg-accent/25 dark:bg-accent/30'
          }`}
          style={{ width: `${displayPct}%` }}
        />
      )}
      <div className="relative z-10 flex h-full items-center justify-between gap-2 px-2 text-xs text-text-secondary dark:text-text-secondary-dark">
        <span>存储空间</span>
        <span className="min-w-0 truncate font-mono tabular-nums">
          {valueText}
        </span>
      </div>
    </div>
  );
}
