'use client';

import { useUIStore } from '@/stores/uiStore';
import CreateToolUnitForm from '@/components/forms/CreateToolUnitForm';
import ToolUnitDetailForm from '@/components/forms/ToolUnitDetailForm';

export default function ToolUnitDetailPanel() {
  const view = useUIStore((s) => s.toolUnitRightView);

  if (view.type === 'edit-unit') {
    return <ToolUnitDetailForm key={view.unitName} unitName={view.unitName} />;
  }

  if (view.type === 'create-unit') {
    return <CreateToolUnitForm />;
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
