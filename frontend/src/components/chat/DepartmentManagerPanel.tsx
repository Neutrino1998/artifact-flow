'use client';

import { useCallback, useEffect, useState } from 'react';
import * as api from '@/lib/api';
import { ApiError } from '@/lib/api';
import type { DepartmentTreeNode } from '@/types';
import { useUIStore } from '@/stores/uiStore';
import DepartmentTreeView from '@/components/chat/DepartmentTreeView';
import DepartmentDetailForm from '@/components/forms/DepartmentDetailForm';
import CreateDepartmentForm from '@/components/forms/CreateDepartmentForm';
import PanelShell from '@/components/layout/PanelShell';

type InnerView =
  | { type: 'tree' }
  | { type: 'edit'; deptId: string }
  | { type: 'create'; parentId: string | null };

export default function DepartmentManagerPanel() {
  const setRightView = useUIStore((s) => s.setUserManagementRightView);
  const bumpListVersion = useUIStore((s) => s.bumpUserMgmtListVersion);

  const [innerView, setInnerView] = useState<InnerView>({ type: 'tree' });
  const [tree, setTree] = useState<DepartmentTreeNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  /**
   * 折叠的部门 id 集合 — 默认空（全展开）。在 panel 这一层维护，避免树切到
   * edit/create 内嵌视图时被卸载、回来全部还原成展开。
   */
  const [collapsedIds, setCollapsedIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const toggleCollapsed = useCallback((deptId: string) => {
    setCollapsedIds((prev) => {
      const next = new Set(prev);
      if (next.has(deptId)) next.delete(deptId);
      else next.add(deptId);
      return next;
    });
  }, []);
  const expandAll = useCallback(() => setCollapsedIds(new Set()), []);
  const collapseAll = useCallback(() => {
    const next = new Set<string>();
    const walk = (ns: DepartmentTreeNode[]): void => {
      for (const n of ns) {
        if ((n.children ?? []).length > 0) {
          next.add(n.id);
          walk(n.children!);
        }
      }
    };
    walk(tree);
    setCollapsedIds(next);
  }, [tree]);
  /**
   * 部门写入版本号 — 每次创建/改名/搬家/删除后 bump，子组件（DetailForm /
   * Cascader）订阅触发自身重拉。本地 state 不上 uiStore，避免污染全局
   * （部门管理面板生命周期内部的事）。
   */
  const [deptWriteVersion, setDeptWriteVersion] = useState(0);

  const reloadTree = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const r = await api.getDepartmentTree();
      setTree(r.nodes);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : '加载部门树失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reloadTree();
  }, [reloadTree]);

  const handleAfterWrite = useCallback(async () => {
    setDeptWriteVersion((v) => v + 1);
    bumpListVersion(); // 让 UserManagementPanel 的部门名 cache 也刷新
    await reloadTree();
  }, [bumpListVersion, reloadTree]);

  const renderHeader = (): React.ReactNode => (
    <div className="flex items-center justify-between gap-3">
      <div>
        <div className="text-base font-semibold text-text-primary dark:text-text-primary-dark">
          部门管理
        </div>
        <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
          点击节点编辑，hover 节点 + 子部门
        </div>
      </div>
      <button
        onClick={() => setRightView({ type: 'empty' })}
        className="flex-shrink-0 p-1 rounded-lg text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors"
        aria-label="关闭"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
          <path d="M4 4l8 8M12 4l-8 8" />
        </svg>
      </button>
    </div>
  );

  // === Inner view: edit ===
  if (innerView.type === 'edit') {
    return (
      <DepartmentDetailForm
        key={innerView.deptId}
        deptId={innerView.deptId}
        refreshKey={deptWriteVersion}
        onChanged={async () => {
          await handleAfterWrite();
        }}
        onDeleted={async () => {
          await handleAfterWrite();
          setInnerView({ type: 'tree' });
        }}
        onBack={() => setInnerView({ type: 'tree' })}
      />
    );
  }

  // === Inner view: create ===
  if (innerView.type === 'create') {
    return (
      <CreateDepartmentForm
        key={`create-${innerView.parentId ?? 'root'}`}
        defaultParentId={innerView.parentId}
        refreshKey={deptWriteVersion}
        onCreated={async (newId) => {
          await handleAfterWrite();
          setInnerView({ type: 'edit', deptId: newId });
        }}
        onBack={() => setInnerView({ type: 'tree' })}
      />
    );
  }

  // === Inner view: tree ===
  return (
    <PanelShell header={renderHeader()}>
      <div className="px-4 pt-4">
        <button
          onClick={() => setInnerView({ type: 'create', parentId: null })}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-accent bg-chat dark:bg-chat-dark rounded-2xl border border-border dark:border-border-dark hover:bg-panel dark:hover:bg-panel-accent-dark transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M7 2v10M2 7h10" />
          </svg>
          新建一级部门
        </button>
      </div>

      {tree.length > 0 && (
        <div className="px-6 pt-2 flex items-center gap-3 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          <button
            onClick={expandAll}
            type="button"
            className="hover:text-accent transition-colors"
          >
            全部展开
          </button>
          <span className="opacity-40">·</span>
          <button
            onClick={collapseAll}
            type="button"
            className="hover:text-accent transition-colors"
          >
            全部折叠
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {loading && tree.length === 0 ? (
          <div className="py-8 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
            加载中...
          </div>
        ) : loadError ? (
          <div className="py-4 text-sm text-status-error">
            {loadError} <button onClick={reloadTree} className="underline">重试</button>
          </div>
        ) : (
          <DepartmentTreeView
            nodes={tree}
            onSelect={(deptId) => setInnerView({ type: 'edit', deptId })}
            onCreateChild={(parentId) => setInnerView({ type: 'create', parentId })}
            collapsedIds={collapsedIds}
            onToggleCollapsed={toggleCollapsed}
          />
        )}
      </div>
    </PanelShell>
  );
}
