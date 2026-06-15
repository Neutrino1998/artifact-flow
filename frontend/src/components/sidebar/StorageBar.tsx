'use client';

import { useEffect, useState } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { getStorageUsage } from '@/lib/api';
import { formatBytes } from '@/lib/formatBytes';
import type { StorageUsageResponse } from '@/types';

// Per-user blob storage indicator in the sidebar footer.
//
// The displayed used/quota always comes from the authoritative GET /chat/storage
// (an index-only SUM over the user's blobs) — never from the paginated sidebar
// list, which only holds the first page. We subscribe to the conversation list
// purely as a *change signal*: deleting a conversation (removeConversation drops
// total + bytes) and an upload completing (COMPLETE refreshes the list with new
// upload_bytes) both shift [total, bytesSum], which re-pulls the real total.
export default function StorageBar() {
  const total = useConversationStore((s) => s.total);
  const bytesSum = useConversationStore((s) =>
    s.conversations.reduce((acc, c) => acc + (c.upload_bytes || 0), 0)
  );

  const [usage, setUsage] = useState<StorageUsageResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStorageUsage()
      .then((u) => {
        if (!cancelled) setUsage(u);
      })
      .catch(() => {
        // Non-critical chrome — on failure just render nothing this cycle.
        if (!cancelled) setUsage(null);
      });
    return () => {
      cancelled = true;
    };
  }, [total, bytesSum]);

  if (!usage) return null;

  const { used_bytes, quota_bytes } = usage;
  const unlimited = quota_bytes <= 0;
  const pct = unlimited
    ? 0
    : Math.min(100, Math.round((used_bytes / quota_bytes) * 100));
  const near = !unlimited && pct >= 90;

  return (
    <div className="px-2 pt-1 pb-0.5">
      <div className="flex items-center justify-between text-xs text-text-tertiary dark:text-text-tertiary-dark mb-1">
        <span>存储空间</span>
        <span className="font-mono tabular-nums">
          {unlimited
            ? formatBytes(used_bytes)
            : `${formatBytes(used_bytes)} / ${formatBytes(quota_bytes)}`}
        </span>
      </div>
      {!unlimited && (
        <div className="h-1 rounded-full bg-bg dark:bg-bg-dark overflow-hidden">
          <div
            className={`h-full transition-[width] duration-200 ease-out ${
              near ? 'bg-status-error' : 'bg-accent'
            }`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}
