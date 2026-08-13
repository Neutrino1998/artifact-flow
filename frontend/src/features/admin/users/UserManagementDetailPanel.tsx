'use client';

import { useUIStore } from '@/stores/uiStore';
import UserDetailForm from './UserDetailForm';
import CreateUserForm from './CreateUserForm';
import BulkImportForm from './BulkImportForm';
import BulkActionPanel from './BulkActionPanel';
import DepartmentManagerPanel from '@/features/admin/departments/DepartmentManagerPanel';

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

  if (view.type === 'bulk-action') {
    return <BulkActionPanel />;
  }

  // empty
  return (
    <div className="flex-1 flex flex-col min-h-0 items-center justify-center bg-chat dark:bg-chat-dark p-6">
      <div className="text-sm text-text-tertiary dark:text-text-tertiary-dark text-center">
        从中间列表选择用户查看详情，
        <br />
        或从左侧栏新建用户 / 管理部门
      </div>
    </div>
  );
}
