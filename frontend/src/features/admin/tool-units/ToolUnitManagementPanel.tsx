'use client';

import { useCallback, useEffect, useState } from 'react';
import { MENU_ROW_HOVER } from '@/lib/styles';
import * as api from '@/lib/api';
import { triggerBlobDownload } from '@/lib/download';
import type { ToolUnitResponse } from '@/types';
import { useUIStore } from '@/stores/uiStore';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import { SourceBadge } from './ToolUnitBadges';
import { PillBadge } from '@/components/ui/PillBadge';
import { StatusNotice } from '@/components/ui/StatusNotice';
import DangerConfirmModal, { DangerConfirmTarget } from '@/components/layout/DangerConfirmModal';
import PanelSearchBar from '@/components/ui/PanelSearchBar';

export default function ToolUnitManagementPanel() {
  const [units, setUnits] = useState<ToolUnitResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [deleteTarget, setDeleteTarget] = useState<ToolUnitResponse | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  const setActiveMode = useUIStore((s) => s.setActiveMode);
  const setRightView = useUIStore((s) => s.setToolUnitRightView);
  const rightView = useUIStore((s) => s.toolUnitRightView);
  const listVersion = useUIStore((s) => s.toolUnitListVersion);
  const bumpListVersion = useUIStore((s) => s.bumpToolUnitListVersion);
  const claim = useLatestOnly();

  // external 工具数量级小 — 后端全量返回,前端做即时过滤,不分页。
  const fetchUnits = useCallback(async () => {
    const isLatest = claim();
    setLoading(true);
    setError(null);
    try {
      const res = await api.listToolUnits();
      if (!isLatest()) return;
      setUnits(res.units);
    } catch (err) {
      if (!isLatest()) return;
      setError(err instanceof Error ? err.message : '加载工具 unit 列表失败');
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [claim]);

  useEffect(() => {
    fetchUnits();
  }, [fetchUnits, listVersion]);

  const selectedName = rightView.type === 'edit-unit' ? rightView.unitName : null;

  const handleExport = useCallback(async (unit: ToolUnitResponse) => {
    setRowError(null);
    setPending((p) => new Set(p).add(unit.name));
    try {
      const blob = await api.downloadToolUnitSeedBundle(unit.name);
      triggerBlobDownload(`${unit.name}-tool-seed.zip`, blob);
    } catch (err) {
      setRowError(err instanceof Error ? err.message : '导出失败');
    } finally {
      setPending((p) => {
        const n = new Set(p);
        n.delete(unit.name);
        return n;
      });
    }
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    const unit = deleteTarget;
    if (!unit) return;
    setRowError(null);
    setPending((p) => new Set(p).add(unit.name));
    try {
      await api.deleteToolUnit(unit.name);
      setUnits((list) => list.filter((u) => u.name !== unit.name));
      bumpListVersion();
      if (selectedName === unit.name) {
        setRightView({ type: 'empty' });
      }
      setDeleteTarget(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : '删除失败';
      setRowError(message);
      if (err instanceof Error) throw err;
      throw new Error(message);
    } finally {
      setPending((p) => {
        const n = new Set(p);
        n.delete(unit.name);
        return n;
      });
    }
  }, [bumpListVersion, deleteTarget, selectedName, setRightView]);

  const q = query.trim().toLowerCase();
  const filtered = q
    ? units.filter(
        (u) =>
          u.name.toLowerCase().includes(q) ||
          (u.description ?? '').toLowerCase().includes(q) ||
          (u.provider ?? '').toLowerCase().includes(q) ||
          u.members.some((m) => m.full_name.toLowerCase().includes(q)),
      )
    : units;

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={setQuery}
        placeholder="搜索 unit 名 / 描述 / 工具全名…"
        countLabel={`${units.length} unit`}
        onClose={() => setActiveMode('none')}
      />

      <div className="flex-1 overflow-y-auto px-4 pb-4">
        <div className="max-w-3xl mx-auto pt-3 space-y-2">
          {error && (
            <StatusNotice tone="error">
              {error}
            </StatusNotice>
          )}

          {rowError && (
            <StatusNotice tone="error" onDismiss={() => setRowError(null)}>
              {rowError}
            </StatusNotice>
          )}

          {loading && units.length === 0 ? (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              加载中...
            </div>
          ) : filtered.length === 0 ? (
            <div className="py-12 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
              {query ? '没有找到匹配的 unit' : '暂无工具 unit'}
            </div>
          ) : (
            filtered.map((u) => (
              <UnitRow
                key={u.name}
                unit={u}
                isSelected={u.name === selectedName}
                busy={pending.has(u.name)}
                onOpen={() => setRightView({ type: 'edit-unit', unitName: u.name })}
                onExport={() => handleExport(u)}
                onDelete={() => setDeleteTarget(u)}
              />
            ))
          )}
        </div>
      </div>

      {deleteTarget && (
        <DangerConfirmModal
          title="删除工具 unit"
          message={'将删除该 unit 的定义、动态 agent 挂载与已配置凭证。\n操作不可恢复。'}
          confirmLabel="确认删除"
          onCancel={() => setDeleteTarget(null)}
          onConfirm={handleConfirmDelete}
        >
          <DangerConfirmTarget
            name={deleteTarget.name}
            description={deleteTarget.description}
          />
        </DangerConfirmModal>
      )}
    </div>
  );
}

function UnitRow({
  unit,
  isSelected,
  busy,
  onOpen,
  onExport,
  onDelete,
}: {
  unit: ToolUnitResponse;
  isSelected: boolean;
  busy: boolean;
  onOpen: () => void;
  onExport: () => void;
  onDelete: () => void;
}) {
  const configuredCreds = unit.credentials.filter((c) => c.configured).length;
  const kindLabel = getUnitKindLabel(unit);
  const isDynamic = unit.source === 'dynamic';

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
      title="点击查看 / 编辑"
      className={`flex items-center gap-3 px-4 py-3 rounded-lg transition-colors mb-1 cursor-pointer ${
        isSelected
          ? 'bg-panel dark:bg-panel-accent-dark'
          : MENU_ROW_HOVER
      }`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-medium font-mono text-text-primary dark:text-text-primary-dark truncate">
            {unit.name}
          </span>
          <SourceBadge source={unit.source} />
          {unit.defer && (
            <PillBadge>defer</PillBadge>
          )}
        </div>
        <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
          {kindLabel}
          {unit.description && <span className="ml-2">{unit.description}</span>}
        </div>
      </div>

      {unit.mounted_agents.length > 0 && (
        <span className="hidden sm:inline flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          {unit.mounted_agents.length} agent
        </span>
      )}

      {unit.credentials.length > 0 && (
        <span
          className={`hidden sm:inline flex-shrink-0 text-xs ${
            configuredCreds === unit.credentials.length
              ? 'text-status-success'
              : 'text-status-warning'
          }`}
        >
          凭证 {configuredCreds}/{unit.credentials.length}
        </span>
      )}

      <div className="flex items-center gap-1 flex-shrink-0">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onExport();
          }}
          disabled={busy}
          className="h-7 w-7 flex items-center justify-center rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label={`导出工具 unit ${unit.name} 的 seed`}
          title="导出 seed bundle"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M8 2v8M4.5 6.5L8 10l3.5-3.5M2.5 13h11" />
          </svg>
        </button>

        {isDynamic && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            disabled={busy}
            className="h-7 w-7 flex items-center justify-center rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-status-error hover:bg-status-error/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            aria-label={`删除工具 unit ${unit.name}`}
            title="删除该动态 unit"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M2.5 4h11M6.5 4V2.5h3V4M4 4l.8 9.5h6.4L12 4M6.5 7v4M9.5 7v4" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}

function getUnitKindLabel(unit: Pick<ToolUnitResponse, 'kind' | 'members'>) {
  return unit.kind === 'mcp'
    ? 'MCP server'
    : unit.kind === 'tool'
      ? '单工具'
      : `工具集 · ${unit.members.length} 工具`;
}
