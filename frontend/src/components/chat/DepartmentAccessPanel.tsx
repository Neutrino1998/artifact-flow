'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import type {
  DepartmentAccessResponse,
  DepartmentSkillAccessItem,
  DepartmentTreeNode,
  DepartmentUnitAccessItem,
} from '@/types';
import { useUIStore } from '@/stores/uiStore';
import DepartmentTreeView from '@/components/chat/DepartmentTreeView';
import { PillBadge } from '@/components/ui/PillBadge';
import { SegmentedTabs } from '@/components/ui/SegmentedTabs';
import { SwitchTrack } from '@/components/ui/SwitchTrack';

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
  const claimAccess = useLatestOnly();
  const [tree, setTree] = useState<DepartmentTreeNode[]>([]);
  const [selectedDeptId, setSelectedDeptId] = useState<string | null>(null);
  const selectedDeptIdRef = useRef<string | null>(null);
  const [collapsedIds, setCollapsedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [treeLoading, setTreeLoading] = useState(true);
  const [treeError, setTreeError] = useState<string | null>(null);

  const [access, setAccess] = useState<DepartmentAccessResponse | null>(null);
  const [accessLoading, setAccessLoading] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [tab, setTab] = useState<AccessTab>('skills');
  const [query, setQuery] = useState('');
  const [pending, setPending] = useState<ReadonlySet<string>>(() => new Set());
  selectedDeptIdRef.current = selectedDeptId;

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

  const reloadAccess = useCallback(async (
    deptId: string,
    errorMessage = '加载部门授权失败',
  ) => {
    const isLatest = claimAccess();
    setAccessLoading(true);
    setAccessError(null);
    try {
      const res = await api.getDepartmentAccess(deptId);
      if (!isLatest()) return;
      setAccess(res);
    } catch (err) {
      if (!isLatest()) return;
      setAccessError(err instanceof ApiError ? err.message : errorMessage);
      setAccess(null);
    } finally {
      if (isLatest()) setAccessLoading(false);
    }
  }, [claimAccess]);

  useEffect(() => {
    void reloadTree();
  }, [reloadTree]);

  useEffect(() => {
    if (!selectedDeptId) {
      claimAccess();
      setAccess(null);
      setAccessError(null);
      setAccessLoading(false);
      return;
    }
    void reloadAccess(selectedDeptId);
  }, [claimAccess, reloadAccess, selectedDeptId]);

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
    const deptId = selectedDeptId;
    if (!deptId) return;
    if (item.inherited_rule) return;

    const id = itemId(tab, item);
    const key = `${deptId}:${tab}:${id}`;
    setPending((prev) => new Set(prev).add(key));
    setAccessError(null);
    try {
      if (tab === 'skills') {
        if (item.direct_rule) await api.deleteDepartmentSkillRule(deptId, id);
        else await api.putDepartmentSkillRule(deptId, id);
      } else if (item.direct_rule) {
        await api.deleteDepartmentUnitRule(deptId, id);
      } else {
        await api.putDepartmentUnitRule(deptId, id);
      }
      if (selectedDeptIdRef.current === deptId) {
        await reloadAccess(deptId, '更新部门规则失败');
      }
    } catch (err) {
      if (selectedDeptIdRef.current === deptId) {
        setAccessError(err instanceof ApiError ? err.message : '更新部门规则失败');
      }
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }, [reloadAccess, selectedDeptId, tab]);

  const removeDirectRule = useCallback(async (item: AccessItem) => {
    const deptId = selectedDeptId;
    if (!deptId || !item.direct_rule) return;

    const id = itemId(tab, item);
    const key = `${deptId}:${tab}:${id}`;
    setPending((prev) => new Set(prev).add(key));
    setAccessError(null);
    try {
      if (tab === 'skills') {
        await api.deleteDepartmentSkillRule(deptId, id);
      } else {
        await api.deleteDepartmentUnitRule(deptId, id);
      }
      if (selectedDeptIdRef.current === deptId) {
        await reloadAccess(deptId, '移除本部门规则失败');
      }
    } catch (err) {
      if (selectedDeptIdRef.current === deptId) {
        setAccessError(err instanceof ApiError ? err.message : '移除本部门规则失败');
      }
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }, [reloadAccess, selectedDeptId, tab]);

  const resourceLabel = tab === 'skills' ? '技能' : '工具 unit';
  const resourceCount = tab === 'skills' ? access?.skills.length ?? 0 : access?.units.length ?? 0;
  const selectedDepartmentTitle = access
    ? access.department.name
    : selectedDeptId
      ? '加载部门中...'
      : '选择部门';

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      <div className="flex-1 min-h-0 flex flex-col lg:flex-row">
        <aside className="max-h-[min(42vh,24rem)] lg:max-h-none lg:w-80 lg:flex-shrink-0 border-b lg:border-b-0 lg:border-r border-border dark:border-border-dark flex flex-col min-h-0 overflow-hidden">
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
              <div className="min-w-0 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3">
                <div className="min-w-0 text-sm font-semibold text-text-primary dark:text-text-primary-dark truncate">
                  {selectedDepartmentTitle}
                </div>
                <SegmentedTabs
                  ariaLabel="部门授权资源类型"
                  value={tab}
                  options={[
                    { value: 'skills', label: '技能' },
                    { value: 'units', label: '工具 unit' },
                  ]}
                  onChange={setTab}
                />
              </div>
              <div className="flex-1" />
              <div className="w-full md:max-w-sm bg-surface dark:bg-surface-dark border border-border dark:border-border-dark focus-within:border-accent dark:focus-within:border-accent rounded-lg px-3 py-2 flex items-center gap-2">
                <svg
                  className="flex-shrink-0 text-text-tertiary dark:text-text-tertiary-dark"
                  width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
                >
                  <circle cx="7" cy="7" r="5" />
                  <path d="M11 11l3.5 3.5" />
                </svg>
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="搜索资源名 / 描述 / 类型..."
                  className="min-w-0 flex-1 bg-transparent text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none"
                />
                <span className="flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                  {access ? `${resourceCount} ${resourceLabel}` : selectedDeptId ? '加载中' : '未选择'}
                </span>
              </div>
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
                  const pendingKey = `${selectedDeptId}:${tab}:${id}`;
                  return (
                    <ResourceRow
                      key={key}
                      tab={tab}
                      item={item}
                      pending={pending.has(pendingKey)}
                      onMutate={() => mutateRule(item)}
                      onClearDirectRule={() => removeDirectRule(item)}
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
  onClearDirectRule,
}: {
  tab: AccessTab;
  item: AccessItem;
  pending: boolean;
  onMutate: () => void;
  onClearDirectRule: () => void;
}) {
  const id = itemId(tab, item);
  const label = itemLabel(tab, item);
  const hasInheritedRule = !!item.inherited_rule;
  const canMutate = !hasInheritedRule;
  const switchChecked = item.effective_allowed;
  const switchTitle = hasInheritedRule
    ? `由 ${item.inherited_rule?.department_name} 继承生效，当前部门不能单独改变可用性`
    : switchChecked
      ? '点击后该部门不可用'
      : '点击后该部门可用';
  const switchAriaLabel = hasInheritedRule
    ? `${label}${switchChecked ? '可用' : '不可用'}，由 ${item.inherited_rule?.department_name} 继承生效`
    : `${switchChecked ? '设为不可用' : '设为可用'}：${label}`;

  return (
    <div className="grid grid-cols-[minmax(340px,1fr)_max-content_48px] items-center gap-3 px-4 py-3 rounded-lg bg-surface dark:bg-surface-dark border border-border/70 dark:border-border-dark/70">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <span className="min-w-0 text-sm font-medium text-text-primary dark:text-text-primary-dark truncate">
            {label}
          </span>
          <SourceBadge source={item.source} />
          <VisibilityBadge visibility={item.visibility} />
          {'kind' in item && <PillBadge>{unitKindLabel(item.kind)}</PillBadge>}
          {'default_enabled' in item && (
            <PillBadge tone={item.default_enabled ? 'success' : 'neutral'}>
              {item.default_enabled ? '默认开' : '默认关'}
            </PillBadge>
          )}
        </div>
        <div className="mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark truncate">
          <span className="font-mono">{id}</span>
          {item.description && <span className="ml-2">{item.description}</span>}
        </div>
      </div>

      <div className="justify-self-start flex items-center gap-1.5 min-w-0">
        <RuleState item={item} pending={pending} onClearDirectRule={onClearDirectRule} />
        <PillBadge tone={item.effective_allowed ? 'success' : 'error'} size="regular">
          {item.effective_allowed ? '可用' : '不可用'}
        </PillBadge>
      </div>

      <button
        type="button"
        role="switch"
        aria-checked={switchChecked}
        aria-label={switchAriaLabel}
        onClick={onMutate}
        disabled={!canMutate || pending}
        title={pending ? '处理中...' : switchTitle}
        className="justify-self-end inline-flex items-center rounded-md p-1 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <SwitchTrack checked={switchChecked} />
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

function RuleState({
  item,
  pending,
  onClearDirectRule,
}: {
  item: AccessItem;
  pending: boolean;
  onClearDirectRule: () => void;
}) {
  const action = item.rule_action === 'deny' ? '排除' : '允许';
  const tone = item.rule_action === 'deny' ? 'error' : 'success';
  if (item.direct_rule) {
    return (
      <div className="flex items-center gap-1.5 min-w-0">
        <PillBadge tone={tone} size="regular">本部门{action}</PillBadge>
        {item.inherited_rule && (
          <span className="text-xs text-text-tertiary dark:text-text-tertiary-dark whitespace-nowrap">
            父级也生效
          </span>
        )}
        {item.inherited_rule && (
          <button
            type="button"
            onClick={onClearDirectRule}
            disabled={pending}
            title="移除本部门规则；父级规则仍会生效"
            className="text-xs whitespace-nowrap text-text-tertiary dark:text-text-tertiary-dark hover:text-accent disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {pending ? '移除中...' : '移除本部门规则'}
          </button>
        )}
      </div>
    );
  }
  if (item.inherited_rule) {
    return (
      <PillBadge tone={tone} size="regular" title={`由 ${item.inherited_rule.department_name} 继承生效`}>
        父级{action} · {item.inherited_rule.department_name}
      </PillBadge>
    );
  }
  return null;
}

function unitKindLabel(kind: string): string {
  if (kind === 'mcp') return 'MCP';
  if (kind === 'toolset') return '工具集';
  return '工具';
}
