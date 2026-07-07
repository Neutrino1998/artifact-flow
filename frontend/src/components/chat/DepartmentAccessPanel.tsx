'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { BUTTON_DANGER_OUTLINE, BUTTON_PRIMARY, BUTTON_SECONDARY, INPUT_ON_PANEL } from '@/lib/styles';
import type {
  DepartmentAccessResponse,
  DepartmentSkillAccessItem,
  DepartmentTreeNode,
  DepartmentUnitAccessItem,
} from '@/types';
import { useUIStore } from '@/stores/uiStore';
import DepartmentTreeView from '@/components/chat/DepartmentTreeView';
import { PillBadge } from '@/components/ui/PillBadge';

type AccessTab = 'skills' | 'units';
type AccessItem = DepartmentSkillAccessItem | DepartmentUnitAccessItem;

function firstDepartmentId(nodes: DepartmentTreeNode[]): string | null {
  for (const node of nodes) return node.id;
  return null;
}

function collectDepartmentIds(nodes: DepartmentTreeNode[], out = new Set<string>()): Set<string> {
  for (const node of nodes) {
    out.add(node.id);
    if (node.children?.length) collectDepartmentIds(node.children, out);
  }
  return out;
}

function collectCollapsibleDepartmentIds(nodes: DepartmentTreeNode[], out = new Set<string>()): Set<string> {
  for (const node of nodes) {
    if (node.children?.length) {
      out.add(node.id);
      collectCollapsibleDepartmentIds(node.children, out);
    }
  }
  return out;
}

function itemId(tab: AccessTab, item: AccessItem): string {
  return tab === 'skills'
    ? (item as DepartmentSkillAccessItem).slug
    : (item as DepartmentUnitAccessItem).name;
}

function itemLabel(tab: AccessTab, item: AccessItem): string {
  return tab === 'skills'
    ? (item as DepartmentSkillAccessItem).name
    : (item as DepartmentUnitAccessItem).name;
}

function matchesQuery(tab: AccessTab, item: AccessItem, query: string): boolean {
  if (!query) return true;
  const unitKind = 'kind' in item ? item.kind : '';
  const enabled = 'default_enabled' in item ? (item.default_enabled ? 'default on' : 'default off') : '';
  const haystack = [
    itemId(tab, item),
    itemLabel(tab, item),
    item.description,
    item.visibility,
    item.source,
    item.rule_action,
    unitKind,
    enabled,
  ].join(' ').toLowerCase();
  return haystack.includes(query);
}

export default function DepartmentAccessPanel() {
  const setActiveMode = useUIStore((s) => s.setActiveMode);
  const [tree, setTree] = useState<DepartmentTreeNode[]>([]);
  const [selectedDeptId, setSelectedDeptId] = useState<string | null>(null);
  const [collapsedIds, setCollapsedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [treeLoading, setTreeLoading] = useState(true);
  const [treeError, setTreeError] = useState<string | null>(null);

  const [access, setAccess] = useState<DepartmentAccessResponse | null>(null);
  const [accessLoading, setAccessLoading] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [tab, setTab] = useState<AccessTab>('skills');
  const [query, setQuery] = useState('');
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());

  const reloadTree = useCallback(async () => {
    setTreeLoading(true);
    setTreeError(null);
    try {
      const res = await api.getDepartmentTree();
      const ids = collectDepartmentIds(res.nodes);
      setTree(res.nodes);
      setSelectedDeptId((current) =>
        current && ids.has(current) ? current : firstDepartmentId(res.nodes),
      );
    } catch (err) {
      setTreeError(err instanceof ApiError ? err.message : '加载部门树失败');
    } finally {
      setTreeLoading(false);
    }
  }, []);

  const reloadAccess = useCallback(async (deptId: string) => {
    setAccessLoading(true);
    setAccessError(null);
    try {
      const res = await api.getDepartmentAccess(deptId);
      setAccess(res);
    } catch (err) {
      setAccessError(err instanceof ApiError ? err.message : '加载部门授权失败');
      setAccess(null);
    } finally {
      setAccessLoading(false);
    }
  }, []);

  useEffect(() => {
    void reloadTree();
  }, [reloadTree]);

  useEffect(() => {
    if (!selectedDeptId) {
      setAccess(null);
      return;
    }
    let cancelled = false;
    setAccessLoading(true);
    setAccessError(null);
    api.getDepartmentAccess(selectedDeptId)
      .then((res) => {
        if (!cancelled) setAccess(res);
      })
      .catch((err) => {
        if (!cancelled) {
          setAccessError(err instanceof ApiError ? err.message : '加载部门授权失败');
          setAccess(null);
        }
      })
      .finally(() => {
        if (!cancelled) setAccessLoading(false);
      });
    return () => { cancelled = true; };
  }, [selectedDeptId]);

  const expandAll = useCallback(() => setCollapsedIds(new Set()), []);
  const collapseAll = useCallback(() => {
    setCollapsedIds(collectCollapsibleDepartmentIds(tree));
  }, [tree]);
  const toggleCollapsed = useCallback((deptId: string) => {
    setCollapsedIds((prev) => {
      const next = new Set(prev);
      if (next.has(deptId)) next.delete(deptId);
      else next.add(deptId);
      return next;
    });
  }, []);

  const activeItems = useMemo(() => {
    const items = tab === 'skills' ? access?.skills ?? [] : access?.units ?? [];
    const q = query.trim().toLowerCase();
    return items.filter((item) => matchesQuery(tab, item, q));
  }, [access, query, tab]);

  const mutateRule = useCallback(async (item: AccessItem) => {
    if (!selectedDeptId) return;
    if (item.inherited_rule && !item.direct_rule) return;

    const id = itemId(tab, item);
    const key = `${tab}:${id}`;
    setPending((prev) => new Set(prev).add(key));
    setAccessError(null);
    try {
      if (tab === 'skills') {
        if (item.direct_rule) await api.deleteDepartmentSkillRule(selectedDeptId, id);
        else await api.putDepartmentSkillRule(selectedDeptId, id);
      } else if (item.direct_rule) {
        await api.deleteDepartmentUnitRule(selectedDeptId, id);
      } else {
        await api.putDepartmentUnitRule(selectedDeptId, id);
      }
      await reloadAccess(selectedDeptId);
    } catch (err) {
      setAccessError(err instanceof ApiError ? err.message : '更新部门规则失败');
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }, [reloadAccess, selectedDeptId, tab]);

  const totalCount = (access?.skills.length ?? 0) + (access?.units.length ?? 0);

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <header className="px-6 py-4 border-b border-border dark:border-border-dark">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-base font-semibold text-text-primary dark:text-text-primary-dark">
              部门授权
            </h1>
            <div className="mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
              {access ? `${access.department.name} · ${totalCount} 项资源` : '选择部门'}
            </div>
          </div>
          <button
            onClick={() => setActiveMode('none')}
            className="flex-shrink-0 p-1.5 rounded-md text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark hover:bg-text-primary/5 dark:hover:bg-text-primary-dark/10 transition-colors"
            aria-label="退出部门授权"
            title="退出"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>
      </header>

      <div className="flex-1 min-h-0 flex flex-col lg:flex-row">
        <aside className="lg:w-80 lg:flex-shrink-0 border-b lg:border-b-0 lg:border-r border-border dark:border-border-dark flex flex-col min-h-0">
          <div className="px-4 py-3 flex items-center justify-between gap-3">
            <div className="text-sm font-medium text-text-primary dark:text-text-primary-dark">
              部门
            </div>
            {tree.length > 0 && (
              <div className="flex items-center gap-2 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                <button type="button" onClick={expandAll} className="hover:text-accent transition-colors">
                  展开
                </button>
                <span className="opacity-40">/</span>
                <button type="button" onClick={collapseAll} className="hover:text-accent transition-colors">
                  折叠
                </button>
              </div>
            )}
          </div>
          <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-4">
            {treeLoading && tree.length === 0 ? (
              <div className="py-8 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
                加载中...
              </div>
            ) : treeError ? (
              <div className="py-4 px-2 text-sm text-status-error">
                {treeError} <button type="button" onClick={reloadTree} className="underline">重试</button>
              </div>
            ) : (
              <DepartmentTreeView
                nodes={tree}
                selectedId={selectedDeptId}
                onSelect={setSelectedDeptId}
                showCreateChild={false}
                collapsedIds={collapsedIds}
                onToggleCollapsed={toggleCollapsed}
              />
            )}
          </div>
        </aside>

        <main className="flex-1 min-w-0 min-h-0 flex flex-col">
          <div className="px-4 py-3 border-b border-border dark:border-border-dark">
            <div className="flex flex-col md:flex-row md:items-center gap-3">
              <div className="inline-flex rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark p-0.5 w-fit">
                <TabButton active={tab === 'skills'} onClick={() => setTab('skills')}>
                  技能
                </TabButton>
                <TabButton active={tab === 'units'} onClick={() => setTab('units')}>
                  工具 unit
                </TabButton>
              </div>
              <div className="flex-1" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索资源名 / 描述 / 类型..."
                className={`${INPUT_ON_PANEL} md:max-w-sm`}
              />
            </div>
          </div>

          {(accessError || accessLoading || !selectedDeptId || activeItems.length === 0) ? (
            <ResourceState
              selectedDeptId={selectedDeptId}
              loading={accessLoading}
              error={accessError}
              empty={activeItems.length === 0 && !!access && !accessLoading && !accessError}
              query={query}
            />
          ) : (
            <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3">
              <div className="min-w-[760px] space-y-1">
                {activeItems.map((item) => {
                  const id = itemId(tab, item);
                  const key = `${tab}:${id}`;
                  return (
                    <ResourceRow
                      key={key}
                      tab={tab}
                      item={item}
                      pending={pending.has(key)}
                      onMutate={() => mutateRule(item)}
                    />
                  );
                })}
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
        active
          ? 'bg-bg dark:bg-bg-dark text-text-primary dark:text-text-primary-dark shadow-sm'
          : 'text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark'
      }`}
    >
      {children}
    </button>
  );
}

function ResourceState({
  selectedDeptId,
  loading,
  error,
  empty,
  query,
}: {
  selectedDeptId: string | null;
  loading: boolean;
  error: string | null;
  empty: boolean;
  query: string;
}) {
  let text = '请选择一个部门';
  if (loading) text = '加载中...';
  else if (error) text = error;
  else if (!selectedDeptId) text = '暂无部门';
  else if (empty) text = query ? '没有找到匹配的资源' : '暂无可配置资源';

  return (
    <div className={`flex-1 flex items-center justify-center px-6 text-sm ${
      error ? 'text-status-error' : 'text-text-tertiary dark:text-text-tertiary-dark'
    }`}>
      {text}
    </div>
  );
}

function ResourceRow({
  tab,
  item,
  pending,
  onMutate,
}: {
  tab: AccessTab;
  item: AccessItem;
  pending: boolean;
  onMutate: () => void;
}) {
  const id = itemId(tab, item);
  const label = itemLabel(tab, item);
  const inheritedOnly = !!item.inherited_rule && !item.direct_rule;
  const canMutate = !inheritedOnly;
  const actionLabel = actionText(item);
  const actionClass = item.direct_rule
    ? BUTTON_SECONDARY
    : item.rule_action === 'deny'
      ? BUTTON_DANGER_OUTLINE
      : BUTTON_PRIMARY;

  return (
    <div className="grid grid-cols-[minmax(220px,1fr)_170px_190px_110px_120px] items-center gap-3 px-4 py-3 rounded-lg bg-surface dark:bg-surface-dark border border-border/70 dark:border-border-dark/70">
      <div className="min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium text-text-primary dark:text-text-primary-dark truncate">
            {label}
          </span>
          <SourceBadge source={item.source} />
        </div>
        <div className="mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
          <span className="font-mono">{id}</span>
          {item.description && <span className="ml-2">{item.description}</span>}
        </div>
      </div>

      <div className="flex items-center gap-1.5 min-w-0">
        <VisibilityBadge visibility={item.visibility} />
        {'kind' in item && <PillBadge>{unitKindLabel(item.kind)}</PillBadge>}
        {'default_enabled' in item && (
          <PillBadge tone={item.default_enabled ? 'success' : 'neutral'}>
            {item.default_enabled ? '默认开' : '默认关'}
          </PillBadge>
        )}
      </div>

      <RuleState item={item} />

      <PillBadge tone={item.effective_allowed ? 'success' : 'error'} size="regular">
        {item.effective_allowed ? '可用' : '不可用'}
      </PillBadge>

      <button
        type="button"
        onClick={onMutate}
        disabled={!canMutate || pending}
        title={inheritedOnly ? `由 ${item.inherited_rule?.department_name} 继承生效` : actionLabel}
        className={`min-w-24 px-3 py-1.5 rounded-md text-xs ${inheritedOnly ? BUTTON_SECONDARY : actionClass}`}
      >
        {pending ? '处理中...' : inheritedOnly ? '继承生效' : actionLabel}
      </button>
    </div>
  );
}

function SourceBadge({ source }: { source: string }) {
  return (
    <PillBadge tone={source === 'dynamic' ? 'accent' : 'neutral'}>
      {source}
    </PillBadge>
  );
}

function VisibilityBadge({ visibility }: { visibility: 'public' | 'department' }) {
  return (
    <PillBadge tone={visibility === 'department' ? 'accent' : 'neutral'}>
      {visibility}
    </PillBadge>
  );
}

function RuleState({ item }: { item: AccessItem }) {
  const action = item.rule_action === 'deny' ? '排除' : '允许';
  if (item.direct_rule) {
    return (
      <div className="flex items-center gap-1.5 min-w-0">
        <PillBadge tone={item.rule_action === 'deny' ? 'error' : 'success'}>本部门{action}</PillBadge>
        {item.inherited_rule && (
          <span className="text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
            父级也生效
          </span>
        )}
      </div>
    );
  }
  if (item.inherited_rule) {
    return (
      <div className="min-w-0 text-xs text-text-secondary dark:text-text-secondary-dark truncate">
        父级{action} · {item.inherited_rule.department_name}
      </div>
    );
  }
  return (
    <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
      无例外
    </div>
  );
}

function actionText(item: AccessItem): string {
  if (item.direct_rule) {
    return item.rule_action === 'deny' ? '取消排除' : '取消允许';
  }
  return item.rule_action === 'deny' ? '排除该部门' : '允许该部门';
}

function unitKindLabel(kind: string): string {
  if (kind === 'mcp') return 'MCP';
  if (kind === 'toolset') return '工具集';
  return '工具';
}
