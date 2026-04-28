import { describe, test, expect, beforeEach } from 'vitest';
import {
  interleaveFlowItems,
  useStreamStore,
  type ExecutionSegment,
  type NonAgentBlock,
  type CompactionBlock,
  type InjectBlock,
  type ToolCallInfo,
} from './streamStore';

function seg(id: string, overrides: Partial<ExecutionSegment> = {}): ExecutionSegment {
  return {
    id,
    agent: 'lead',
    status: 'complete',
    reasoningContent: '',
    isThinking: false,
    toolCalls: [],
    content: '',
    llmOutput: '',
    ...overrides,
  };
}

function inject(id: string, position: number): InjectBlock {
  return { kind: 'inject', id, content: 'msg', timestamp: 't', position };
}

function compaction(id: string, position: number, state: 'running' | 'done' | 'error' = 'done'): CompactionBlock {
  return { kind: 'compaction', id, state, timestamp: 't', position };
}

describe('interleaveFlowItems', () => {
  test('empty + empty → []', () => {
    expect(interleaveFlowItems([], [])).toEqual([]);
  });

  test('segments with no blocks → all agent items', () => {
    const segs = [seg('s1'), seg('s2')];
    const out = interleaveFlowItems(segs, []);
    expect(out).toHaveLength(2);
    expect(out.every(item => item.kind === 'agent')).toBe(true);
  });

  test('block.position=0 → block placed before first segment', () => {
    const out = interleaveFlowItems([seg('s1')], [inject('i1', 0)]);
    expect(out[0].kind).toBe('inject');
    expect(out[1].kind).toBe('agent');
  });

  test('block.position > segments.length → trailing tail', () => {
    const out = interleaveFlowItems([seg('s1')], [inject('i1', 5)]);
    // The block wasn't placed during the segment loop (position > i for all i),
    // so it ends up appended after segments
    expect(out).toHaveLength(2);
    expect(out[0].kind).toBe('agent');
    expect(out[1].kind).toBe('inject');
  });

  test('out-of-order block input → output sorted by position', () => {
    const blocks = [inject('late', 2), inject('early', 0)];
    const out = interleaveFlowItems([seg('s1'), seg('s2'), seg('s3')], blocks);
    const flow = out
      .map(item => (item.kind === 'agent' ? `seg:${item.segment.id}` : `${item.kind}:${item.id}`))
      .join(',');
    // early(pos=0) comes first, late(pos=2) comes between segments
    expect(flow.indexOf('inject:early')).toBeLessThan(flow.indexOf('seg:s1'));
    expect(flow.indexOf('inject:late')).toBeLessThan(flow.indexOf('seg:s3'));
    expect(flow.indexOf('inject:late')).toBeGreaterThan(flow.indexOf('seg:s2'));
  });

  test('inject + compaction blocks at same position both included', () => {
    const out = interleaveFlowItems([seg('s1')], [inject('i1', 0), compaction('c1', 0)]);
    const kinds = out.map(item => (item.kind === 'agent' ? 'agent' : item.kind));
    expect(kinds.filter(k => k === 'inject')).toHaveLength(1);
    expect(kinds.filter(k => k === 'compaction')).toHaveLength(1);
    expect(kinds.filter(k => k === 'agent')).toHaveLength(1);
  });
});

describe('streamStore actions', () => {
  beforeEach(() => {
    // Reset to a clean baseline before each test
    useStreamStore.setState({
      segments: [],
      nonAgentBlocks: [],
    });
  });

  describe('updateNonAgentBlock', () => {
    test('matching id and kind=compaction → patch merged', () => {
      const block: CompactionBlock = compaction('c1', 0, 'running');
      useStreamStore.setState({ nonAgentBlocks: [block] });

      useStreamStore.getState().updateNonAgentBlock('c1', { state: 'done', summary: 'hi' });

      const updated = useStreamStore.getState().nonAgentBlocks[0] as CompactionBlock;
      expect(updated.state).toBe('done');
      expect(updated.summary).toBe('hi');
      expect(updated.id).toBe('c1');
    });

    test('id matches but kind=inject → unchanged (compaction-only patch)', () => {
      const block: InjectBlock = inject('i1', 0);
      useStreamStore.setState({ nonAgentBlocks: [block] });

      useStreamStore.getState().updateNonAgentBlock('i1', { state: 'done' });

      const after = useStreamStore.getState().nonAgentBlocks[0];
      expect(after).toEqual(block); // reference content unchanged
      expect(after.kind).toBe('inject');
    });

    test('id not found → all blocks unchanged', () => {
      const blocks: NonAgentBlock[] = [compaction('c1', 0, 'running'), inject('i1', 1)];
      useStreamStore.setState({ nonAgentBlocks: blocks });

      useStreamStore.getState().updateNonAgentBlock('ghost', { state: 'done' });

      expect(useStreamStore.getState().nonAgentBlocks).toEqual(blocks);
    });
  });

  describe('updateToolCallInSegment', () => {
    test('updates tool call in earlier segment (not just current)', () => {
      const tcA: ToolCallInfo = {
        id: 'tc-a', toolName: 'a', params: {}, agent: 'lead', status: 'running',
      };
      const tcB: ToolCallInfo = {
        id: 'tc-b', toolName: 'b', params: {}, agent: 'lead', status: 'running',
      };
      useStreamStore.setState({
        segments: [
          seg('s1', { toolCalls: [tcA] }),
          seg('s2', { toolCalls: [tcB] }),
        ],
      });

      useStreamStore.getState().updateToolCallInSegment('tc-a', { status: 'success', result: 'ok' });

      const segs = useStreamStore.getState().segments;
      expect(segs[0].toolCalls[0]).toMatchObject({ id: 'tc-a', status: 'success', result: 'ok' });
      // tc-b unchanged
      expect(segs[1].toolCalls[0]).toMatchObject({ id: 'tc-b', status: 'running' });
    });
  });

  describe('snapshotSegments', () => {
    test('filters segments without toolCalls or reasoning, forces running→complete', () => {
      const segs: ExecutionSegment[] = [
        seg('s-empty', { status: 'running' }),                                       // dropped
        seg('s-running-with-tool', { status: 'running', toolCalls: [{ id: 't', toolName: 'x', params: {}, agent: '', status: 'running' }] }),
        seg('s-with-reasoning', { reasoningContent: 'thoughts' }),
      ];
      useStreamStore.setState({ segments: segs });

      useStreamStore.getState().snapshotSegments('msg-1');

      const snap = useStreamStore.getState().completedSegments.get('msg-1');
      expect(snap).toBeDefined();
      expect(snap).toHaveLength(2);
      expect(snap!.map(s => s.id)).toEqual(['s-running-with-tool', 's-with-reasoning']);
      // Running segment forced to complete in snapshot
      expect(snap!.find(s => s.id === 's-running-with-tool')!.status).toBe('complete');
    });

    test('snapshots non-agent blocks in full (no filtering)', () => {
      const blocks: NonAgentBlock[] = [
        inject('i1', 0),
        compaction('c1', 1, 'done'),
      ];
      useStreamStore.setState({
        segments: [seg('s1', { reasoningContent: 'r' })],
        nonAgentBlocks: blocks,
      });

      useStreamStore.getState().snapshotSegments('msg-2');

      const blockSnap = useStreamStore.getState().completedNonAgentBlocks.get('msg-2');
      expect(blockSnap).toEqual(blocks);
    });

    test('no segments to snapshot → completedSegments unchanged', () => {
      useStreamStore.setState({
        segments: [seg('s-empty')],   // will be filtered out
        nonAgentBlocks: [],
      });
      const before = useStreamStore.getState().completedSegments;

      useStreamStore.getState().snapshotSegments('msg-3');

      // No new entry written because nothing meaningful to snapshot
      expect(useStreamStore.getState().completedSegments).toBe(before);
      expect(useStreamStore.getState().completedSegments.has('msg-3')).toBe(false);
    });
  });
});
