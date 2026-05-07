'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import type { DepartmentTreeNode } from '@/types';

interface DepartmentCascaderProps {
  /** 当前选中末级 dept_id；null = 未分配 */
  value: string | null;
  onChange: (deptId: string | null) => void;
  /**
   * 搬家场景：禁选给定 dept 自己 + 所有子孙（防止环）
   */
  excludeSubtreeOf?: string;
  /** 显示 "+ 新建当前级" 选项（admin 创建用户/部门管理时用） */
  allowCreate?: boolean;
  disabled?: boolean;
  /** 触发外部刷新（PR4 部门管理 panel 在新建/搬家后需要 cascader 同步树） */
  refreshKey?: number;
}

const CREATE_TOKEN = '__create__';
const LEVEL_LABELS = ['一级', '二级', '三级', '四级', '五级', '六级', '七级', '八级', '九级', '十级'];
const MAX_DEPTH = LEVEL_LABELS.length;

interface FlatNode {
  id: string;
  name: string;
  parent_id: string | null;
  children: FlatNode[];
}

function flattenTree(nodes: DepartmentTreeNode[]): {
  rootChildren: FlatNode[];
  byId: Map<string, FlatNode>;
} {
  const byId = new Map<string, FlatNode>();
  function walk(n: DepartmentTreeNode): FlatNode {
    const flat: FlatNode = {
      id: n.id,
      name: n.name,
      parent_id: n.parent_id ?? null,
      children: [],
    };
    byId.set(n.id, flat);
    flat.children = (n.children ?? []).map(walk);
    return flat;
  }
  const rootChildren = nodes.map(walk);
  return { rootChildren, byId };
}

function ancestorChain(leafId: string, byId: Map<string, FlatNode>): string[] {
  const chain: string[] = [];
  let cursor: string | null = leafId;
  while (cursor) {
    const node = byId.get(cursor);
    if (!node) break;
    chain.unshift(node.id);
    cursor = node.parent_id;
  }
  return chain;
}

function expandSubtree(seedId: string, byId: Map<string, FlatNode>): Set<string> {
  const result = new Set<string>();
  const seed = byId.get(seedId);
  if (!seed) return result;
  const stack: FlatNode[] = [seed];
  while (stack.length) {
    const cur = stack.pop()!;
    if (result.has(cur.id)) continue;
    result.add(cur.id);
    stack.push(...cur.children);
  }
  return result;
}

export default function DepartmentCascader({
  value,
  onChange,
  excludeSubtreeOf,
  allowCreate = false,
  disabled = false,
  refreshKey,
}: DepartmentCascaderProps) {
  const [tree, setTree] = useState<{
    rootChildren: FlatNode[];
    byId: Map<string, FlatNode>;
  }>({ rootChildren: [], byId: new Map() });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [creatingAtParent, setCreatingAtParent] = useState<{ parentId: string | null } | null>(null);
  const [createName, setCreateName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const r = await api.getDepartmentTree();
      setTree(flattenTree(r.nodes));
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : '加载部门失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload, refreshKey]);

  const excludedIds = useMemo(
    () => (excludeSubtreeOf ? expandSubtree(excludeSubtreeOf, tree.byId) : new Set<string>()),
    [excludeSubtreeOf, tree.byId],
  );

  // value → path (root → leaf)
  const path = useMemo(() => (value ? ancestorChain(value, tree.byId) : []), [value, tree.byId]);

  // 渲染 path.length + 1 个 select：每级显示选中 dept 的 children
  // 第 0 级显示根，第 i 级显示 path[i-1] 的 children（i > 0）
  // path.length 级是"再选下一级（可选）"，没有 children 则不渲染
  const levelOptions: { parentId: string | null; options: FlatNode[] }[] = useMemo(() => {
    const levels: { parentId: string | null; options: FlatNode[] }[] = [];
    // Level 0
    levels.push({ parentId: null, options: tree.rootChildren });
    // Subsequent levels — under each chosen dept；上限 MAX_DEPTH 级
    //
    // 即使选中的是叶子（无子部门），也要为它再 push 一层空 options 的 select。
    // 这样 allowCreate=true 时用户能在那一级用 "+ 在此层级新建" 创建叶子的子，
    // 而当前层级的 "+ 新建" 创建的是 sibling — 两个意图都能表达。
    for (const chosen of path) {
      if (levels.length >= MAX_DEPTH) break;
      const node = tree.byId.get(chosen);
      if (!node) break;
      levels.push({ parentId: node.id, options: node.children });
    }
    return levels;
  }, [path, tree]);

  const handleSelectChange = (levelIndex: number, parentId: string | null, raw: string) => {
    if (raw === CREATE_TOKEN) {
      setCreatingAtParent({ parentId });
      setCreateName('');
      setCreateError(null);
      return;
    }
    if (raw === '') {
      // 不选 — 清掉本级及之后
      if (levelIndex === 0) {
        onChange(null);
      } else {
        // 选中变为本级的父
        onChange(path[levelIndex - 1] ?? null);
      }
      return;
    }
    onChange(raw);
  };

  const handleCreateSubmit = async () => {
    const name = createName.trim();
    if (!name || !creatingAtParent) return;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await api.createDepartment({
        name,
        parent_id: creatingAtParent.parentId,
      });
      // 重新拉树并选中新建项
      await reload();
      onChange(created.id);
      setCreatingAtParent(null);
      setCreateName('');
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : '创建失败');
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">加载部门...</div>;
  }
  if (loadError) {
    return (
      <div className="text-xs text-status-error">
        {loadError} <button onClick={reload} className="underline">重试</button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {levelOptions.map((level, i) => {
        const selected = path[i] ?? '';
        const visibleOptions = level.options.filter((o) => !excludedIds.has(o.id));
        return (
          <div key={`level-${i}-${level.parentId ?? 'root'}`} className="flex items-center gap-2">
            <span className="text-xs w-12 flex-shrink-0 text-text-tertiary dark:text-text-tertiary-dark">
              {LEVEL_LABELS[i]}
            </span>
            <div className="relative flex-1">
              <select
                value={selected}
                onChange={(e) => handleSelectChange(i, level.parentId, e.target.value)}
                disabled={disabled || creating}
                className="w-full appearance-none px-3 py-1.5 pr-9 rounded-lg bg-surface dark:bg-surface-dark border border-border dark:border-border-dark text-sm text-text-primary dark:text-text-primary-dark focus:outline-none focus:border-accent disabled:opacity-40"
              >
                <option value="">— 不选 —</option>
                {visibleOptions.map((o) => (
                  <option key={o.id} value={o.id}>{o.name}</option>
                ))}
                {allowCreate && (
                  <option value={CREATE_TOKEN}>+ 在此层级新建...</option>
                )}
              </select>
              <svg
                className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary dark:text-text-tertiary-dark"
                width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
              >
                <path d="M3 4.5l3 3 3-3" />
              </svg>
            </div>
          </div>
        );
      })}

      {creatingAtParent && (
        <div className="ml-14 flex items-center gap-2">
          <input
            type="text"
            autoFocus
            value={createName}
            onChange={(e) => setCreateName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreateSubmit();
              if (e.key === 'Escape') setCreatingAtParent(null);
            }}
            disabled={creating}
            placeholder="新部门名称"
            className="flex-1 px-3 py-1.5 rounded-lg bg-surface dark:bg-surface-dark border border-border dark:border-border-dark text-sm text-text-primary dark:text-text-primary-dark focus:outline-none focus:border-accent disabled:opacity-40"
          />
          <button
            onClick={handleCreateSubmit}
            disabled={creating || !createName.trim()}
            className="px-3 py-1.5 rounded-lg bg-accent text-white text-sm hover:bg-accent-hover disabled:opacity-40 transition-colors"
          >
            {creating ? '创建中...' : '创建'}
          </button>
          <button
            onClick={() => { setCreatingAtParent(null); setCreateName(''); setCreateError(null); }}
            disabled={creating}
            className="px-2 py-1.5 text-sm text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark"
          >
            取消
          </button>
        </div>
      )}
      {createError && (
        <div className="ml-14 text-xs text-status-error">{createError}</div>
      )}
    </div>
  );
}
