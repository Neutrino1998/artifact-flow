import { describe, expect, test } from 'vitest';
import { buildMessageTree, extractBranchPath } from './messageTree';
import { resolveStreamingDisplayPath } from './messageDisplayPath';
import type { MessageResponse } from '@/types';

function msg(id: string, parent_id: string | null = null): MessageResponse {
  return {
    id,
    parent_id,
    user_input: id,
    response: null,
    created_at: '2026-01-01T00:00:00Z',
    children: [],
    execution_metrics: null,
    uploaded_files: null,
    active_skills: null,
  };
}

describe('resolveStreamingDisplayPath', () => {
  test('reconnected persisted active message keeps the full branch path', () => {
    const nodeMap = buildMessageTree([msg('parent'), msg('active', 'parent')]);
    const branchPath = extractBranchPath(nodeMap, 'active');

    const displayPath = resolveStreamingDisplayPath(branchPath, true, undefined);

    expect(displayPath.map((n) => n.id)).toEqual(['parent', 'active']);
  });

  test('local pending branch send trims to the requested parent', () => {
    const nodeMap = buildMessageTree([
      msg('root'),
      msg('parent', 'root'),
      msg('old-leaf', 'parent'),
    ]);
    const branchPath = extractBranchPath(nodeMap, 'old-leaf');

    const displayPath = resolveStreamingDisplayPath(branchPath, true, 'parent');

    expect(displayPath.map((n) => n.id)).toEqual(['root', 'parent']);
  });

  test('root rerun hides prior persisted messages while pending bubble streams', () => {
    const nodeMap = buildMessageTree([msg('root'), msg('old-leaf', 'root')]);
    const branchPath = extractBranchPath(nodeMap, 'old-leaf');

    expect(resolveStreamingDisplayPath(branchPath, true, null)).toEqual([]);
  });
});
