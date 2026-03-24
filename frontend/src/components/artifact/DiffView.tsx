'use client';

import { memo, useMemo, useState, useCallback } from 'react';
import { diffLines, type Change } from 'diff';

interface DiffViewProps {
  oldContent: string;
  newContent: string;
}

/** Number of context lines to show around each change */
const CONTEXT_LINES = 3;

interface DiffLine {
  type: 'added' | 'removed' | 'unchanged';
  content: string;
  oldLineNo: number;
  newLineNo: number;
}

interface DiffHunk {
  kind: 'lines' | 'collapsed';
  lines: DiffLine[];
  collapsedCount?: number;
}

function computeHunks(oldContent: string, newContent: string): DiffHunk[] {
  const changes: Change[] = diffLines(oldContent, newContent);

  // Build flat line list — track both old and new file line numbers
  const allLines: DiffLine[] = [];
  let oldLine = 1;
  let newLine = 1;

  for (const change of changes) {
    const lines = change.value.split('\n');
    if (lines[lines.length - 1] === '') lines.pop();

    for (const line of lines) {
      if (change.added) {
        allLines.push({ type: 'added', content: line, oldLineNo: -1, newLineNo: newLine++ });
      } else if (change.removed) {
        allLines.push({ type: 'removed', content: line, oldLineNo: oldLine++, newLineNo: -1 });
      } else {
        allLines.push({ type: 'unchanged', content: line, oldLineNo: oldLine++, newLineNo: newLine++ });
      }
    }
  }

  // Mark indices within CONTEXT_LINES of any change as visible
  const changedIndices = new Set<number>();
  allLines.forEach((line, i) => {
    if (line.type !== 'unchanged') {
      for (
        let j = Math.max(0, i - CONTEXT_LINES);
        j <= Math.min(allLines.length - 1, i + CONTEXT_LINES);
        j++
      ) {
        changedIndices.add(j);
      }
    }
  });

  const hunks: DiffHunk[] = [];
  let i = 0;

  while (i < allLines.length) {
    if (changedIndices.has(i)) {
      const lines: DiffLine[] = [];
      while (i < allLines.length && changedIndices.has(i)) {
        lines.push(allLines[i]);
        i++;
      }
      hunks.push({ kind: 'lines', lines });
    } else {
      const lines: DiffLine[] = [];
      while (i < allLines.length && !changedIndices.has(i)) {
        lines.push(allLines[i]);
        i++;
      }
      hunks.push({ kind: 'collapsed', lines, collapsedCount: lines.length });
    }
  }

  return hunks;
}

function CollapsedSection({
  count,
  lines,
}: {
  count: number;
  lines: DiffLine[];
}) {
  const [expanded, setExpanded] = useState(false);
  const toggle = useCallback(() => setExpanded((v) => !v), []);

  if (expanded) {
    return (
      <>
        {lines.map((line, i) => (
          <tr key={i} className="h-[22px]">
            <td className="w-[3px] align-top" />
            <td className="px-2 text-right select-none align-top text-text-tertiary dark:text-text-tertiary-dark w-10">
              {line.oldLineNo > 0 ? line.oldLineNo : ''}
            </td>
            <td className="px-2 text-right select-none align-top text-text-tertiary dark:text-text-tertiary-dark w-10">
              {line.newLineNo > 0 ? line.newLineNo : ''}
            </td>
            <td className="px-3 whitespace-pre-wrap break-all align-top text-text-primary dark:text-text-primary-dark">
              <span className="select-none mr-2 opacity-40">{' '}</span>
              {line.content || '\u00A0'}
            </td>
          </tr>
        ))}
      </>
    );
  }

  return (
    <tr>
      <td colSpan={4} className="py-1.5 px-3">
        <div
          onClick={toggle}
          className="flex items-center justify-center gap-1.5 py-1 px-3 rounded-md cursor-pointer
            border border-border dark:border-border-dark
            bg-bg-secondary/60 dark:bg-bg-secondary-dark/60
            hover:bg-bg-secondary dark:hover:bg-bg-secondary-dark
            text-[11px] text-text-tertiary dark:text-text-tertiary-dark
            transition-colors"
        >
          <svg
            className="w-3.5 h-3.5 opacity-50"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="4 6 8 3 12 6" />
            <polyline points="4 10 8 13 12 10" />
          </svg>
          {count} 行未修改
        </div>
      </td>
    </tr>
  );
}

function DiffView({ oldContent, newContent }: DiffViewProps) {
  const hunks = useMemo(
    () => computeHunks(oldContent, newContent),
    [oldContent, newContent]
  );

  const hasChanges = hunks.some((h) => h.kind === 'lines');
  if (!hasChanges) {
    return (
      <div className="p-8 text-center text-text-tertiary dark:text-text-tertiary-dark">
        该版本暂无变更记录
      </div>
    );
  }

  return (
    <div className="font-mono text-xs">
      <table className="w-full border-collapse">
        <tbody>
          {hunks.map((hunk, hi) =>
            hunk.kind === 'collapsed' ? (
              <CollapsedSection
                key={hi}
                count={hunk.collapsedCount!}
                lines={hunk.lines}
              />
            ) : (
              hunk.lines.map((line, li) => (
                <tr
                  key={`${hi}-${li}`}
                  className={`h-[22px] ${lineClassName(line.type)}`}
                >
                  <td className={`w-[3px] align-top ${gutterClassName(line.type)}`} />
                  <td className="px-2 text-right select-none align-top text-text-tertiary dark:text-text-tertiary-dark w-10">
                    {line.oldLineNo > 0 ? line.oldLineNo : ''}
                  </td>
                  <td className="px-2 text-right select-none align-top text-text-tertiary dark:text-text-tertiary-dark w-10">
                    {line.newLineNo > 0 ? line.newLineNo : ''}
                  </td>
                  <td
                    className={`px-3 whitespace-pre-wrap break-all align-top ${lineTextClassName(line.type)}`}
                  >
                    <span className="select-none mr-2 opacity-60">
                      {line.type === 'added'
                        ? '+'
                        : line.type === 'removed'
                          ? '-'
                          : ' '}
                    </span>
                    {line.content || '\u00A0'}
                  </td>
                </tr>
              ))
            )
          )}
        </tbody>
      </table>
    </div>
  );
}

function gutterClassName(type: DiffLine['type']): string {
  switch (type) {
    case 'added':
      return 'bg-status-success';
    case 'removed':
      return 'bg-status-error';
    default:
      return '';
  }
}

function lineClassName(type: DiffLine['type']): string {
  switch (type) {
    case 'added':
      return 'bg-green-50 dark:bg-green-950/20';
    case 'removed':
      return 'bg-red-50 dark:bg-red-950/20';
    default:
      return '';
  }
}

function lineTextClassName(type: DiffLine['type']): string {
  switch (type) {
    case 'added':
      return 'text-status-success';
    case 'removed':
      return 'text-status-error';
    default:
      return 'text-text-primary dark:text-text-primary-dark';
  }
}

export default memo(DiffView);
