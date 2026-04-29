import { describe, test, expect } from 'vitest';
import { buildMessageTree, extractBranchPath, getBranchChoicesAtMessage } from './messageTree';
import type { MessageResponse } from '@/types';

function msg(id: string, parent_id: string | null = null): MessageResponse {
  return {
    id,
    parent_id,
    user_input: '',
    response: null,
    created_at: '2026-01-01T00:00:00Z',
    children: [],
    execution_metrics: null,
  };
}

describe('buildMessageTree', () => {
  test('empty input → empty map', () => {
    expect(buildMessageTree([]).size).toBe(0);
  });

  test('single root message', () => {
    const map = buildMessageTree([msg('a')]);
    expect(map.size).toBe(1);
    expect(map.get('a')!.childNodes).toEqual([]);
    expect(map.get('a')!.siblingIndex).toBe(0);
    expect(map.get('a')!.siblingCount).toBe(1);
  });

  test('parent + child → linked via childNodes', () => {
    const map = buildMessageTree([msg('a'), msg('b', 'a')]);
    expect(map.get('a')!.childNodes).toHaveLength(1);
    expect(map.get('a')!.childNodes[0].id).toBe('b');
  });

  test('multiple siblings → siblingIndex/siblingCount correct', () => {
    const map = buildMessageTree([msg('a'), msg('b1', 'a'), msg('b2', 'a'), msg('b3', 'a')]);
    const parent = map.get('a')!;
    expect(parent.childNodes).toHaveLength(3);
    expect(parent.childNodes.map(c => c.siblingIndex)).toEqual([0, 1, 2]);
    expect(parent.childNodes.every(c => c.siblingCount === 3)).toBe(true);
  });

  test('multiple roots → root sibling info correct', () => {
    const map = buildMessageTree([msg('r1'), msg('r2'), msg('r3')]);
    expect(map.get('r1')!.siblingCount).toBe(3);
    expect(map.get('r2')!.siblingIndex).toBe(1);
  });

  test('parent_id pointing to non-existent parent → node still created, not linked', () => {
    const map = buildMessageTree([msg('orphan', 'ghost')]);
    expect(map.has('orphan')).toBe(true);
    // orphan was never added to anyone's childNodes
    for (const node of map.values()) {
      expect(node.childNodes.find(c => c.id === 'orphan')).toBeUndefined();
    }
  });
});

describe('extractBranchPath', () => {
  test('empty map → empty array', () => {
    expect(extractBranchPath(new Map(), null)).toEqual([]);
  });

  test('no roots (all messages have parent_id but parents missing) → empty array', () => {
    const map = buildMessageTree([msg('a', 'ghost')]);
    expect(extractBranchPath(map, null)).toEqual([]);
  });

  test('activeBranch found → traces back to root', () => {
    const map = buildMessageTree([msg('a'), msg('b', 'a'), msg('c', 'b')]);
    const path = extractBranchPath(map, 'c');
    expect(path.map(n => n.id)).toEqual(['a', 'b', 'c']);
  });

  test('activeBranch missing from map → fallback to default path', () => {
    const map = buildMessageTree([msg('a'), msg('b', 'a')]);
    const path = extractBranchPath(map, 'nonexistent');
    expect(path.map(n => n.id)).toEqual(['a', 'b']);
  });

  test('no activeBranch + branched tree → follows last-child-of-last-root', () => {
    // Tree:
    //   a
    //   ├── b1
    //   └── b2
    //       └── c
    const map = buildMessageTree([
      msg('a'),
      msg('b1', 'a'),
      msg('b2', 'a'),
      msg('c', 'b2'),
    ]);
    const path = extractBranchPath(map, null);
    expect(path.map(n => n.id)).toEqual(['a', 'b2', 'c']);
  });

  test('linear single-chain conversation', () => {
    const map = buildMessageTree([msg('a'), msg('b', 'a'), msg('c', 'b'), msg('d', 'c')]);
    const path = extractBranchPath(map, undefined);
    expect(path.map(n => n.id)).toEqual(['a', 'b', 'c', 'd']);
  });

  test('multiple roots + no activeBranch → starts from last root', () => {
    const map = buildMessageTree([msg('r1'), msg('r2'), msg('r2-child', 'r2')]);
    const path = extractBranchPath(map, null);
    expect(path.map(n => n.id)).toEqual(['r2', 'r2-child']);
  });
});

describe('getBranchChoicesAtMessage', () => {
  test('messageId not in map → empty siblings', () => {
    const map = buildMessageTree([msg('a')]);
    const r = getBranchChoicesAtMessage(map, 'ghost');
    expect(r.siblings).toEqual([]);
    expect(r.currentIndex).toBe(0);
  });

  test('root-level message → siblings = all roots', () => {
    const map = buildMessageTree([msg('r1'), msg('r2'), msg('r3')]);
    const r = getBranchChoicesAtMessage(map, 'r2');
    expect(r.siblings.map(s => s.id)).toEqual(['r1', 'r2', 'r3']);
    expect(r.currentIndex).toBe(1);
  });

  test('child message → siblings = parent.childNodes', () => {
    const map = buildMessageTree([msg('a'), msg('b1', 'a'), msg('b2', 'a'), msg('b3', 'a')]);
    const r = getBranchChoicesAtMessage(map, 'b2');
    expect(r.siblings.map(s => s.id)).toEqual(['b1', 'b2', 'b3']);
    expect(r.currentIndex).toBe(1);
  });

  test('parent_id points to non-existent parent → empty siblings', () => {
    const map = buildMessageTree([msg('orphan', 'ghost')]);
    const r = getBranchChoicesAtMessage(map, 'orphan');
    expect(r.siblings).toEqual([]);
    expect(r.currentIndex).toBe(0);
  });
});
