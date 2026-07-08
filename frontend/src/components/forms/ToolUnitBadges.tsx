'use client';

import { PillBadge } from '@/components/ui/PillBadge';

// 工具 unit 的小徽章,列表行与详情头共用一份(防止两处对同一 source/state 的文案/配色漂移)。

export function SourceBadge({ source }: { source: string }) {
  const seeded = source === 'seeded';
  return (
    <PillBadge tone={seeded ? 'neutral' : 'accent'}>
      {seeded ? '种子' : '动态'}
    </PillBadge>
  );
}
