'use client';

import { useUIStore } from '@/stores/uiStore';

export default function UserManagementDetailPanel() {
  const view = useUIStore((s) => s.userManagementRightView);

  if (view.type === 'edit-user') {
    return (
      <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark p-6">
        <div className="text-sm text-text-secondary dark:text-text-secondary-dark">
          用户详情
        </div>
        <div className="mt-2 text-xs font-mono text-text-tertiary dark:text-text-tertiary-dark break-all">
          {view.userId}
        </div>
        <div className="mt-6 text-sm text-text-tertiary dark:text-text-tertiary-dark">
          编辑表单将在 PR2b 落地。
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 items-center justify-center bg-chat dark:bg-chat-dark p-6">
      <div className="text-sm text-text-tertiary dark:text-text-tertiary-dark text-center">
        选择左侧用户查看详情，
        <br />
        或点击 + 新建用户
      </div>
    </div>
  );
}
