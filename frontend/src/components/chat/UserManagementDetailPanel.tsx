'use client';

import { useUIStore } from '@/stores/uiStore';
import UserDetailForm from '@/components/forms/UserDetailForm';
import CreateUserForm from '@/components/forms/CreateUserForm';
import BulkImportForm from '@/components/forms/BulkImportForm';
import DepartmentManagerPanel from '@/components/chat/DepartmentManagerPanel';

export default function UserManagementDetailPanel() {
  const view = useUIStore((s) => s.userManagementRightView);

  if (view.type === 'edit-user') {
    return <UserDetailForm key={view.userId} userId={view.userId} />;
  }

  if (view.type === 'create-user') {
    return <CreateUserForm />;
  }

  if (view.type === 'bulk-import') {
    return <BulkImportForm />;
  }

  if (view.type === 'dept-manager') {
    return <DepartmentManagerPanel />;
  }

  // empty / bulk-action（PR5a）— 占位
  return (
    <div className="flex-1 flex flex-col min-h-0 items-center justify-center bg-chat dark:bg-chat-dark p-6">
      <div className="text-sm text-text-tertiary dark:text-text-tertiary-dark text-center">
        选择左侧用户查看详情，
        <br />
        或点击 + 新建用户 / 管理部门
      </div>
    </div>
  );
}
