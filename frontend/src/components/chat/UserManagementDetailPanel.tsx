'use client';

import { useUIStore } from '@/stores/uiStore';
import UserDetailForm from '@/components/forms/UserDetailForm';
import CreateUserForm from '@/components/forms/CreateUserForm';

export default function UserManagementDetailPanel() {
  const view = useUIStore((s) => s.userManagementRightView);

  if (view.type === 'edit-user') {
    return <UserDetailForm key={view.userId} userId={view.userId} />;
  }

  if (view.type === 'create-user') {
    return <CreateUserForm />;
  }

  // empty / create-dept / edit-dept / bulk-action 暂未实现 — 留待后续 PR
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
