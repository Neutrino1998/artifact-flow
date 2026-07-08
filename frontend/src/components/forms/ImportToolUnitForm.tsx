'use client';

import { useState } from 'react';
import * as api from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import { BUTTON_PRIMARY, BUTTON_SECONDARY, INPUT_ON_PANEL, LABEL_CLASS } from '@/lib/styles';
import PanelShell from '@/components/layout/PanelShell';

export default function ImportToolUnitForm() {
  const setRightView = useUIStore((s) => s.setToolUnitRightView);
  const bumpListVersion = useUIStore((s) => s.bumpToolUnitListVersion);

  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!file || submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const imported = await api.importToolUnitSeed(file);
      bumpListVersion();
      setRightView({
        type: 'edit-unit',
        unitName: imported.unit.name,
        showMountReminder: true,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : '导入失败');
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
              导入工具 seed
            </div>
            <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
              上传后创建为动态 unit
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
            disabled={!file || submitting}
            type="button"
            className={`${BUTTON_PRIMARY} rounded-lg px-6 py-2`}
          >
            {submitting ? '导入中…' : '导入'}
          </button>
        </>
      }
    >
      <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        <div>
          <label className={LABEL_CLASS}>seed 文件</label>
          <input
            type="file"
            accept=".zip,.md,text/markdown,application/zip"
            disabled={submitting}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className={INPUT_ON_PANEL}
          />
          {file && (
            <div className="mt-2 text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
              {file.name}
            </div>
          )}
        </div>

        {error && <div className="text-status-error text-sm">{error}</div>}
      </div>
    </PanelShell>
  );
}
