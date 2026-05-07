'use client';

import { useState } from 'react';
import type { DepartmentTreeNode } from '@/types';

interface DepartmentTreeViewProps {
  nodes: DepartmentTreeNode[];
  selectedId?: string | null;
  onSelect: (deptId: string) => void;
  onCreateChild: (parentId: string) => void;
}

export default function DepartmentTreeView({
  nodes,
  selectedId,
  onSelect,
  onCreateChild,
}: DepartmentTreeViewProps) {
  if (nodes.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
        暂无部门，点击上方按钮新建第一个一级部门
      </div>
    );
  }
  return (
    <ul className="space-y-0.5">
      {nodes.map((n) => (
        <TreeNodeItem
          key={n.id}
          node={n}
          depth={0}
          selectedId={selectedId ?? null}
          onSelect={onSelect}
          onCreateChild={onCreateChild}
        />
      ))}
    </ul>
  );
}

function TreeNodeItem({
  node,
  depth,
  selectedId,
  onSelect,
  onCreateChild,
}: {
  node: DepartmentTreeNode;
  depth: number;
  selectedId: string | null;
  onSelect: (deptId: string) => void;
  onCreateChild: (parentId: string) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = (node.children ?? []).length > 0;
  const isSelected = node.id === selectedId;

  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        onClick={() => onSelect(node.id)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onSelect(node.id);
          }
        }}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        className={`group flex items-center gap-2 pr-2 py-1.5 rounded-lg transition-colors cursor-pointer ${
          isSelected
            ? 'bg-panel dark:bg-panel-accent-dark'
            : 'hover:bg-panel/60 dark:hover:bg-panel-accent-dark/60'
        }`}
      >
        {hasChildren ? (
          <button
            onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
            className="flex-shrink-0 w-4 h-4 flex items-center justify-center text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary"
            aria-label={expanded ? '折叠' : '展开'}
          >
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5" className={expanded ? 'rotate-90' : ''}>
              <path d="M3 2l4 3-4 3z" fill="currentColor" />
            </svg>
          </button>
        ) : (
          <span className="flex-shrink-0 w-4 h-4 inline-flex items-center justify-center opacity-50 text-base leading-none">•</span>
        )}

        <span className="flex-1 text-sm text-text-primary dark:text-text-primary-dark truncate">
          {node.name}
        </span>

        <span className="flex-shrink-0 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          {node.user_count} 人
        </span>

        <button
          onClick={(e) => { e.stopPropagation(); onCreateChild(node.id); }}
          className="flex-shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100 px-1.5 py-0.5 text-xs rounded text-accent hover:bg-accent/10 transition-opacity"
          title="在此部门下新建子部门"
        >
          + 子
        </button>
      </div>

      {hasChildren && expanded && (
        <ul className="space-y-0.5">
          {node.children!.map((c) => (
            <TreeNodeItem
              key={c.id}
              node={c}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              onCreateChild={onCreateChild}
            />
          ))}
        </ul>
      )}
    </li>
  );
}
