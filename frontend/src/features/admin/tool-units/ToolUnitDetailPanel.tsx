'use client';

import { useUIStore } from '@/stores/uiStore';
import CreateToolUnitForm from './CreateToolUnitForm';
import ImportToolUnitForm from './ImportToolUnitForm';
import ToolUnitDetailForm from './ToolUnitDetailForm';

export default function ToolUnitDetailPanel() {
  const view = useUIStore((s) => s.toolUnitRightView);

  if (view.type === 'edit-unit') {
    return (
      <ToolUnitDetailForm
        key={view.unitName}
        unitName={view.unitName}
        initialShowMountReminder={view.showMountReminder === true}
      />
    );
  }

  if (view.type === 'create-unit') {
    return <CreateToolUnitForm />;
  }

  if (view.type === 'import-unit') {
    return <ImportToolUnitForm />;
  }

  // empty
  return (
    <div className="flex-1 flex flex-col min-h-0 items-center justify-center bg-chat dark:bg-chat-dark p-6">
      <div className="text-sm text-text-tertiary dark:text-text-tertiary-dark text-center">
        选择左侧工具 unit 查看 / 编辑，
        <br />
        或点击 + 新建 unit
      </div>
    </div>
  );
}
