'use client';

import { useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from '@/lib/styles';
import PanelShell from '@/components/layout/PanelShell';
import ToolUnitEditor, {
  draftToRequest,
  emptyUnitDraft,
  type UnitDraft,
} from '@/components/forms/ToolUnitEditor';

export default function CreateToolUnitForm() {
  const setRightView = useUIStore((s) => s.setToolUnitRightView);
  const bumpListVersion = useUIStore((s) => s.bumpToolUnitListVersion);

  const [draft, setDraft] = useState<UnitDraft>(emptyUnitDraft);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (submitting) return;
    setError(null);
    // 先做本地 coerce/校验(draftToRequest 抛中文 Error),再发请求。后端仍权威。
    let body;
    try {
      body = draftToRequest(draft);
    } catch (err) {
      setError(err instanceof Error ? err.message : '表单校验失败');
      return;
    }
    setSubmitting(true);
    try {
      const created = await api.createToolUnit(body);
      bumpListVersion();
      // 落到刚建好的 unit 详情:可立即挂载 agent / 配凭证
      setRightView({ type: 'edit-unit', unitName: created.name });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PanelShell
      header={
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-base font-semibold text-text-primary dark:text-text-primary-dark">
              新建工具 unit
            </div>
            <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
              动态注册（dynamic）;config 种子的工具请改 config/tools 后重跑 reconcile
            </div>
          </div>
          <button
            onClick={() => setRightView({ type: 'empty' })}
            disabled={submitting}
            className="flex-shrink-0 p-1 rounded-lg text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark disabled:opacity-40 transition-colors"
            aria-label="关闭"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
      }
      footer={
        <>
          <button
            onClick={() => setRightView({ type: 'empty' })}
            disabled={submitting}
            type="button"
            className={`${BUTTON_SECONDARY} rounded-lg px-6 py-2`}
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            type="button"
            className={`${BUTTON_PRIMARY} rounded-lg px-6 py-2`}
          >
            {submitting ? '创建中...' : '创建'}
          </button>
        </>
      }
    >
      <div className="flex-1 overflow-y-auto px-6 py-5">
        <ToolUnitEditor value={draft} onChange={setDraft} disabled={submitting} />
        {error && <div className="text-status-error text-sm mt-4">{error}</div>}
      </div>
    </PanelShell>
  );
}
