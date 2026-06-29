'use client';

import { useCallback, useEffect, useState } from 'react';
import * as api from '@/lib/api';
import type { ToolUnitResponse } from '@/types';
import { useUIStore } from '@/stores/uiStore';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import PanelSearchBar from './PanelSearchBar';

export default function ToolUnitManagementPanel() {
  const [units, setUnits] = useState<ToolUnitResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const setVisible = useUIStore((s) => s.setToolUnitManagementVisible);
  const setRightView = useUIStore((s) => s.setToolUnitRightView);
  const rightView = useUIStore((s) => s.toolUnitRightView);
  const listVersion = useUIStore((s) => s.toolUnitListVersion);
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

  const q = query.trim().toLowerCase();
  const filtered = q
    ? units.filter(
        (u) =>
          u.name.toLowerCase().includes(q) ||
          (u.description ?? '').toLowerCase().includes(q) ||
          u.members.some((m) => m.full_name.toLowerCase().includes(q)),
      )
    : units;

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <PanelSearchBar
        value={query}
        onChange={setQuery}
        placeholder="搜索 unit 名 / 描述 / 工具全名..."
        countLabel={`${units.length} unit`}
        onClose={() => setVisible(false)}
      />

      <div className="flex-1 overflow-y-auto px-4">
        <div className="max-w-3xl mx-auto">
          {error && (
            <div className="mb-3 px-3 py-2 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-lg">
              {error}
            </div>
          )}

          <div className="mb-3">
            <button
              onClick={() => setRightView({ type: 'create-unit' })}
              className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-2xl border font-medium transition-colors ${
                rightView.type === 'create-unit'
                  ? 'text-accent border-accent bg-bg dark:bg-bg-dark'
                  : 'text-accent border-border dark:border-border-dark bg-surface dark:bg-surface-dark hover:bg-bg dark:hover:bg-bg-dark'
              }`}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M7 2v10M2 7h10" />
              </svg>
              新建工具 unit
            </button>
          </div>

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
                onOpen={() => setRightView({ type: 'edit-unit', unitName: u.name })}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function UnitRow({
  unit,
  isSelected,
  onOpen,
}: {
  unit: ToolUnitResponse;
  isSelected: boolean;
  onOpen: () => void;
}) {
  const seeded = unit.source === 'seeded';
  const configuredCreds = unit.credentials.filter((c) => c.configured).length;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
      title="点击查看 / 编辑"
      className={`flex items-center gap-3 px-4 py-3 rounded-lg transition-colors mb-1 cursor-pointer ${
        isSelected
          ? 'bg-panel dark:bg-panel-accent-dark'
          : 'hover:bg-panel/60 dark:hover:bg-panel-accent-dark/60'
      }`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-medium font-mono text-text-primary dark:text-text-primary-dark truncate">
            {unit.name}
          </span>
          <span
            className={`flex-shrink-0 inline-block px-1.5 py-0.5 text-xs rounded ${
              seeded
                ? 'bg-bg dark:bg-bg-dark text-text-secondary dark:text-text-secondary-dark'
                : 'bg-accent/10 text-accent'
            }`}
          >
            {seeded ? '种子' : '动态'}
          </span>
          {unit.defer && (
            <span className="flex-shrink-0 inline-block px-1.5 py-0.5 text-xs rounded bg-bg dark:bg-bg-dark text-text-tertiary dark:text-text-tertiary-dark">
              defer
            </span>
          )}
        </div>
        <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
          {unit.kind === 'tool' ? '单工具' : `工具集 · ${unit.members.length} 工具`}
          {unit.description && <span className="ml-2">{unit.description}</span>}
        </div>
      </div>

      {/* 挂载数 */}
      {unit.mounted_agents.length > 0 && (
        <span className="flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          {unit.mounted_agents.length} agent
        </span>
      )}

      {/* 凭证状态 */}
      {unit.credentials.length > 0 && (
        <span
          className={`flex-shrink-0 text-xs ${
            configuredCreds === unit.credentials.length
              ? 'text-green-600 dark:text-green-400'
              : 'text-amber-600 dark:text-amber-400'
          }`}
        >
          凭证 {configuredCreds}/{unit.credentials.length}
        </span>
      )}
    </div>
  );
}
