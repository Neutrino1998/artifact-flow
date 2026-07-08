'use client';

import { useCallback, useRef, useState } from 'react';
import * as api from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from '@/lib/styles';
import PanelShell from '@/components/layout/PanelShell';

type ImportStage =
  | { kind: 'pick' }
  | { kind: 'submitting' };

export default function ImportToolUnitForm() {
  const setRightView = useUIStore((s) => s.setToolUnitRightView);
  const bumpListVersion = useUIStore((s) => s.bumpToolUnitListVersion);

  const [stage, setStage] = useState<ImportStage>({ kind: 'pick' });
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const submitting = stage.kind === 'submitting';

  const clearNativeInput = useCallback(() => {
    if (inputRef.current) inputRef.current.value = '';
  }, []);

  const handleFile = useCallback((f: File | null) => {
    setError(null);
    if (!f) {
      setFile(null);
      clearNativeInput();
      return;
    }
    if (!/\.(zip|md)$/i.test(f.name)) {
      setFile(null);
      clearNativeInput();
      setError('请选择 .zip 或 .md seed 文件');
      return;
    }
    setFile(f);
  }, [clearNativeInput]);

  const submit = useCallback(async () => {
    if (!file || submitting) return;
    setStage({ kind: 'submitting' });
    setError(null);
    try {
      const imported = await api.importToolUnitSeed(file);
      bumpListVersion();
      setRightView({
        type: 'edit-unit',
        unitName: imported.unit.name,
        showMountReminder: true,
      });
    } catch (err) {
      setStage({ kind: 'pick' });
      setFile(null);
      clearNativeInput();
      setError(err instanceof Error ? err.message : '导入失败');
    }
  }, [bumpListVersion, clearNativeInput, file, setRightView, submitting]);

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
            title="关闭"
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
            onClick={submit}
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
        <div
          onDragOver={(e) => {
            e.preventDefault();
            if (!submitting) setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragActive(false);
            if (!submitting) handleFile(e.dataTransfer.files?.[0] ?? null);
          }}
          className={`rounded-xl border-2 border-dashed p-5 text-center transition-colors ${
            dragActive
              ? 'border-accent bg-panel/50 dark:bg-panel-accent-dark/50'
              : 'border-border dark:border-border-dark'
          }`}
        >
          {file ? (
            <div className="flex flex-col items-center gap-1">
              <div className="text-sm text-text-primary dark:text-text-primary-dark font-medium break-all">
                {file.name}
              </div>
              <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
                {(file.size / 1024).toFixed(1)} KB
              </div>
              <button
                onClick={() => handleFile(null)}
                type="button"
                disabled={submitting}
                className="mt-1 text-xs text-accent hover:underline disabled:opacity-40 disabled:cursor-not-allowed"
              >
                换一个文件
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-1.5">
              <div className="text-sm text-text-secondary dark:text-text-secondary-dark">
                拖拽 seed 文件到此处
              </div>
              <button
                onClick={() => inputRef.current?.click()}
                type="button"
                disabled={submitting}
                className="px-4 py-1.5 rounded-lg border border-border dark:border-border-dark text-sm font-medium text-text-secondary dark:text-text-secondary-dark bg-surface dark:bg-surface-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                选择文件
              </button>
              <div className="text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
                zip bundle 或单个 .md seed
              </div>
            </div>
          )}
          <input
            ref={inputRef}
            type="file"
            accept=".zip,.md,application/zip"
            disabled={submitting}
            onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
            className="hidden"
          />
        </div>

        {error && <div className="text-status-error text-sm">{error}</div>}
      </div>
    </PanelShell>
  );
}
