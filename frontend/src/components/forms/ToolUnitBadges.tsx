'use client';

// 工具 unit 的小徽章,列表行与详情头共用一份(防止两处对同一 source/state 的文案/配色漂移)。

export function SourceBadge({ source }: { source: string }) {
  const seeded = source === 'seeded';
  return (
    <span
      className={`flex-shrink-0 inline-block px-1.5 py-0.5 text-xs rounded ${
        seeded
          ? 'bg-bg dark:bg-bg-dark text-text-secondary dark:text-text-secondary-dark'
          : 'bg-accent/10 text-accent'
      }`}
    >
      {seeded ? '种子' : '动态'}
    </span>
  );
}

export function StateBadge({ state }: { state: string }) {
  const enabled = state === 'enabled';
  return (
    <span className={`flex-shrink-0 text-xs ${enabled ? 'text-green-600 dark:text-green-400' : 'text-text-tertiary dark:text-text-tertiary-dark'}`}>
      {enabled ? '启用' : '停用'}
    </span>
  );
}
