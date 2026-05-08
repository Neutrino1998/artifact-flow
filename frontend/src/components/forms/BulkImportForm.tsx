'use client';

import { useCallback, useRef, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import type { BulkImportResponse, BulkImportFailedRow } from '@/types';

type Stage =
  | { kind: 'upload' }
  | { kind: 'submitting' }
  | { kind: 'result'; data: BulkImportResponse };

const TEMPLATE_HEADER = 'username,password,display_name,dept_l1,dept_l2,dept_l3';
const TEMPLATE_SAMPLE =
  'alice,,Alice Cooper,部门A,子部门A1,小组A1a\n' +
  'bobby,custompw,,部门A,子部门A1,\n' +
  'carol,,Carol,部门B,,';

function downloadCsv(filename: string, content: string) {
  // Excel 中文环境识别 UTF-8 BOM 为 UTF-8；不带 BOM 会被当 GBK 乱码
  const blob = new Blob(['﻿' + content], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function failedRowsToCsv(rows: BulkImportFailedRow[]): string {
  const header = 'row,username,reason';
  const escape = (v: string | null | undefined): string => {
    const s = v ?? '';
    if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  };
  const body = rows
    .map((r) => `${r.row},${escape(r.username)},${escape(r.reason)}`)
    .join('\n');
  return `${header}\n${body}\n`;
}

export default function BulkImportForm() {
  const setRightView = useUIStore((s) => s.setUserManagementRightView);
  const bumpListVersion = useUIStore((s) => s.bumpUserMgmtListVersion);

  const [stage, setStage] = useState<Stage>({ kind: 'upload' });
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** 文件内重复行 — 后端 400 detail 是 dict，结构化展示 */
  const [duplicateRows, setDuplicateRows] = useState<
    Array<{ row: number; username: string }> | null
  >(null);
  const [dragActive, setDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // 清掉 <input type="file"> 的 DOM 值 —— 否则用户重新选同名文件不触发
  // onChange，"换一个文件 / 再导入一批"流程会卡住（受控之外的 DOM 状态）
  const clearNativeInput = useCallback(() => {
    if (inputRef.current) inputRef.current.value = '';
  }, []);

  const reset = useCallback(() => {
    setStage({ kind: 'upload' });
    setFile(null);
    setError(null);
    setDuplicateRows(null);
    clearNativeInput();
  }, [clearNativeInput]);

  const handleFile = useCallback((f: File | null) => {
    setError(null);
    setDuplicateRows(null);
    if (!f) {
      setFile(null);
      clearNativeInput();
      return;
    }
    // Soft client-side hint — backend is authoritative
    if (!/\.csv$/i.test(f.name) && f.type && !/csv|text/.test(f.type)) {
      setError('请选择 CSV 文件');
      return;
    }
    setFile(f);
  }, [clearNativeInput]);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      const f = e.dataTransfer.files?.[0] ?? null;
      handleFile(f);
    },
    [handleFile],
  );

  const submit = useCallback(async () => {
    if (!file) return;
    setStage({ kind: 'submitting' });
    setError(null);
    setDuplicateRows(null);
    try {
      const data = await api.bulkImportUsers(file);
      setStage({ kind: 'result', data });
      bumpListVersion();
    } catch (err) {
      // 文件内重复 → 结构化展示；其他 → 文案
      if (err instanceof ApiError && err.body && typeof err.body === 'object') {
        const detail = (err.body as { detail?: unknown }).detail;
        if (
          detail &&
          typeof detail === 'object' &&
          Array.isArray((detail as { duplicate_rows?: unknown }).duplicate_rows)
        ) {
          setDuplicateRows(
            (detail as { duplicate_rows: Array<{ row: number; username: string }> })
              .duplicate_rows,
          );
          setError('CSV 文件内 username 重复，请先在源文件去重再上传');
          setStage({ kind: 'upload' });
          return;
        }
      }
      setError(err instanceof Error ? err.message : '导入失败');
      setStage({ kind: 'upload' });
    }
  }, [file, bumpListVersion]);

  const close = useCallback(() => {
    if (stage.kind === 'submitting') return;
    setRightView({ type: 'empty' });
  }, [stage.kind, setRightView]);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      {/* Header */}
      <div className="px-6 pt-5 pb-3 border-b border-border dark:border-border-dark flex items-center justify-between gap-3">
        <div>
          <div className="text-base font-semibold text-text-primary dark:text-text-primary-dark">
            批量导入用户
          </div>
          <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
            上传 CSV 文件；密码留空时默认 = 用户名
          </div>
        </div>
        <button
          onClick={close}
          disabled={stage.kind === 'submitting'}
          className="flex-shrink-0 p-1 rounded-lg text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark disabled:opacity-40 transition-colors"
          aria-label="关闭"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {stage.kind === 'upload' && (
          <UploadStage
            file={file}
            error={error}
            duplicateRows={duplicateRows}
            dragActive={dragActive}
            onDragOver={(e) => {
              e.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={onDrop}
            onPick={() => inputRef.current?.click()}
            onFileChange={(e) => handleFile(e.target.files?.[0] ?? null)}
            onClear={() => handleFile(null)}
            inputRef={inputRef}
            onDownloadTemplate={() =>
              downloadCsv(
                'users-template.csv',
                `${TEMPLATE_HEADER}\n${TEMPLATE_SAMPLE}\n`,
              )
            }
          />
        )}

        {stage.kind === 'submitting' && (
          <div className="py-12 text-center text-sm text-text-secondary dark:text-text-secondary-dark">
            正在导入，请勿关闭...
          </div>
        )}

        {stage.kind === 'result' && (
          <ResultStage
            data={stage.data}
            onDownloadFailed={() =>
              downloadCsv('failed-rows.csv', failedRowsToCsv(stage.data.failed))
            }
            onAnother={reset}
          />
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-border dark:border-border-dark px-6 py-4 flex justify-end gap-3">
        {stage.kind === 'upload' && (
          <>
            <button
              onClick={close}
              type="button"
              className="px-6 py-2 rounded-lg border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            >
              取消
            </button>
            <button
              onClick={submit}
              disabled={!file}
              type="button"
              className="px-6 py-2 rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors"
            >
              开始导入
            </button>
          </>
        )}
        {stage.kind === 'submitting' && (
          <button
            disabled
            type="button"
            className="px-6 py-2 rounded-lg bg-accent text-white opacity-60"
          >
            导入中...
          </button>
        )}
        {stage.kind === 'result' && (
          <>
            <button
              onClick={reset}
              type="button"
              className="px-6 py-2 rounded-lg border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            >
              再导入一批
            </button>
            <button
              onClick={() => setRightView({ type: 'empty' })}
              type="button"
              className="px-6 py-2 rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors"
            >
              完成
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------
// Upload stage
// ------------------------------------------------------------

interface UploadStageProps {
  file: File | null;
  error: string | null;
  duplicateRows: Array<{ row: number; username: string }> | null;
  dragActive: boolean;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent) => void;
  onPick: () => void;
  onFileChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onClear: () => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
  onDownloadTemplate: () => void;
}

function UploadStage({
  file,
  error,
  duplicateRows,
  dragActive,
  onDragOver,
  onDragLeave,
  onDrop,
  onPick,
  onFileChange,
  onClear,
  inputRef,
  onDownloadTemplate,
}: UploadStageProps) {
  return (
    <div className="space-y-4">
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={`rounded-2xl border-2 border-dashed p-8 text-center transition-colors ${
          dragActive
            ? 'border-accent bg-panel/50 dark:bg-panel-accent-dark/50'
            : 'border-border dark:border-border-dark'
        }`}
      >
        {file ? (
          <div className="flex flex-col items-center gap-2">
            <div className="text-sm text-text-primary dark:text-text-primary-dark font-medium">
              {file.name}
            </div>
            <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
              {(file.size / 1024).toFixed(1)} KB
            </div>
            <button
              onClick={onClear}
              type="button"
              className="mt-2 text-xs text-text-secondary dark:text-text-secondary-dark hover:text-accent"
            >
              换一个文件
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-text-tertiary dark:text-text-tertiary-dark">
              <path d="M12 4v12M6 10l6-6 6 6M4 20h16" />
            </svg>
            <div className="text-sm text-text-secondary dark:text-text-secondary-dark">
              拖拽 CSV 文件到此处
            </div>
            <button
              onClick={onPick}
              type="button"
              className="mt-2 px-4 py-1.5 rounded-lg border border-border dark:border-border-dark text-sm text-text-primary dark:text-text-primary-dark hover:bg-panel dark:hover:bg-panel-accent-dark transition-colors"
            >
              选择文件
            </button>
          </div>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          onChange={onFileChange}
          className="hidden"
        />
      </div>

      {error && (
        <div className="text-status-error text-sm">{error}</div>
      )}

      {duplicateRows && duplicateRows.length > 0 && (
        <div className="rounded-lg border border-status-error/40 bg-status-error/5 p-3 text-xs">
          <div className="font-medium text-status-error mb-2">
            文件内重复（{duplicateRows.length} 行）：
          </div>
          <div className="max-h-32 overflow-y-auto font-mono text-text-secondary dark:text-text-secondary-dark space-y-0.5">
            {duplicateRows.map((d, i) => (
              <div key={i}>第 {d.row} 行：{d.username}</div>
            ))}
          </div>
        </div>
      )}

      {/* Format hint + template download */}
      <div className="rounded-lg bg-panel/40 dark:bg-panel-accent-dark/40 p-4 text-xs space-y-2">
        <div className="font-medium text-text-secondary dark:text-text-secondary-dark">
          CSV 格式说明
        </div>
        <ul className="text-text-tertiary dark:text-text-tertiary-dark space-y-1 list-disc pl-4">
          <li>
            <span className="font-mono">username</span>{' '}
            <span className="text-status-error">*</span>（必填，2~64 字符，仅
            字母 / 数字 / <span className="font-mono">. _ -</span>）
          </li>
          <li>
            <span className="font-mono">password</span>（可选，留空则默认 =
            username，最少 4 字符）
          </li>
          <li><span className="font-mono">display_name</span>（可选，可中文）</li>
          <li>
            <span className="font-mono">dept_l1</span> / <span className="font-mono">dept_l2</span> / <span className="font-mono">dept_l3</span>
            （可选，按层级填部门名；缺失层级自动创建。空层不可中间穿插）
          </li>
          <li>编码支持 UTF-8（含 BOM）和 GBK；行数上限 1000</li>
        </ul>
        <button
          onClick={onDownloadTemplate}
          type="button"
          className="mt-1 text-accent hover:underline"
        >
          下载 CSV 模板
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------
// Result stage
// ------------------------------------------------------------

function ResultStage({
  data,
  onDownloadFailed,
  onAnother,
}: {
  data: BulkImportResponse;
  onDownloadFailed: () => void;
  onAnother: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <SummaryCard label="创建" count={data.created.length} accent="success" />
        <SummaryCard label="跳过" count={data.skipped.length} accent="warn" />
        <SummaryCard label="失败" count={data.failed.length} accent="error" />
      </div>

      <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
        总行数 {data.total_rows}
        {data.detected_encoding && <> · 编码 {data.detected_encoding}</>}
      </div>

      {data.warnings.length > 0 && (
        <div className="rounded-lg bg-panel/40 dark:bg-panel-accent-dark/40 p-3 text-xs space-y-1">
          {data.warnings.map((w, i) => (
            <div key={i} className="text-text-secondary dark:text-text-secondary-dark">
              · {w}
            </div>
          ))}
        </div>
      )}

      {data.skipped.length > 0 && (
        <DetailList
          title={`跳过（${data.skipped.length}） — username 已存在`}
          items={data.skipped.map((s) => ({
            row: s.row,
            primary: s.username,
            secondary: s.reason,
          }))}
        />
      )}

      {data.failed.length > 0 && (
        <DetailList
          title={`失败（${data.failed.length}）`}
          items={data.failed.map((f) => ({
            row: f.row,
            primary: f.username ?? '(no username)',
            secondary: f.reason,
          }))}
          variant="error"
          actionLabel="下载失败行 CSV"
          onAction={onDownloadFailed}
        />
      )}

      {/* If everything went sideways and nothing was created, hint to retry */}
      {data.created.length === 0 && data.failed.length + data.skipped.length > 0 && (
        <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
          没有创建任何用户。修复 CSV 后{' '}
          <button onClick={onAnother} className="text-accent hover:underline">
            再试一次
          </button>
        </div>
      )}
    </div>
  );
}

function SummaryCard({
  label,
  count,
  accent,
}: {
  label: string;
  count: number;
  accent: 'success' | 'warn' | 'error';
}) {
  const accentClass =
    accent === 'success'
      ? 'text-green-600 dark:text-green-400'
      : accent === 'warn'
      ? 'text-yellow-600 dark:text-yellow-400'
      : 'text-status-error';
  return (
    <div className="rounded-lg border border-border dark:border-border-dark p-3 text-center">
      <div className={`text-2xl font-semibold ${accentClass}`}>{count}</div>
      <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark mt-1">
        {label}
      </div>
    </div>
  );
}

function DetailList({
  title,
  items,
  variant = 'default',
  actionLabel,
  onAction,
}: {
  title: string;
  items: Array<{ row: number; primary: string; secondary: string }>;
  variant?: 'default' | 'error';
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div
          className={`text-xs font-medium ${
            variant === 'error'
              ? 'text-status-error'
              : 'text-text-secondary dark:text-text-secondary-dark'
          }`}
        >
          {title}
        </div>
        {actionLabel && onAction && (
          <button
            onClick={onAction}
            type="button"
            className="text-xs text-accent hover:underline"
          >
            {actionLabel}
          </button>
        )}
      </div>
      <div className="rounded-lg border border-border dark:border-border-dark divide-y divide-border dark:divide-border-dark max-h-48 overflow-y-auto">
        {items.map((it, i) => (
          <div key={i} className="px-3 py-2 text-xs">
            <div className="flex items-baseline gap-2">
              <span className="text-text-tertiary dark:text-text-tertiary-dark font-mono">
                #{it.row}
              </span>
              <span className="font-medium text-text-primary dark:text-text-primary-dark">
                {it.primary}
              </span>
            </div>
            <div className="text-text-tertiary dark:text-text-tertiary-dark mt-0.5">
              {it.secondary}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
