import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import {
  cancelPendingFlush,
  interleaveFlowItems,
  scheduleContentUpdate,
  useStreamStore,
  type ExecutionSegment,
  type NonAgentBlock,
  type CompactionBlock,
  type InjectBlock,
  type PendingInjectBlock,
  type ToolCallInfo,
} from './streamStore';

function seg(id: string, overrides: Partial<ExecutionSegment> = {}): ExecutionSegment {
  return {
    id,
    agent: 'lead',
    status: 'complete',
    reasoningContent: '',
    llmStreamChannel: null,
    toolCalls: [],
    toolCallProgress: [],
    content: '',
    ...overrides,
  };
}

function inject(id: string, position: number): InjectBlock {
  return { kind: 'inject', id, content: 'msg', timestamp: 't', position };
}

function pendingInject(id: string, position: number): PendingInjectBlock {
  return { kind: 'pending_inject', id, content: 'pending', timestamp: 't', position };
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

  test('pending inject blocks interleave with the live flow but remain distinguishable', () => {
    const out = interleaveFlowItems([seg('s1')], [pendingInject('p1', 1)]);
    expect(out[0].kind).toBe('agent');
    expect(out[1].kind).toBe('pending_inject');
  });
});

describe('streamStore actions', () => {
  beforeEach(() => {
    cancelPendingFlush();
    // Reset all mutable state — including the snapshot Maps — so tests are
    // order-independent. Forgetting completedSegments / completedNonAgentBlocks
    // here would let snapshot entries leak between cases and silently mask
    // regressions in `snapshotSegments` assertions.
    useStreamStore.setState({
      segments: [],
      nonAgentBlocks: [],
      pendingInjects: [],
      completedSegments: new Map(),
      completedNonAgentBlocks: new Map(),
    });
  });

  afterEach(() => {
    cancelPendingFlush();
    vi.unstubAllGlobals();
  });

  test('RAF content snapshot remains bound to its originating segment', () => {
    let flush: FrameRequestCallback | undefined;
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
      flush = callback;
      return 1;
    }));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    useStreamStore.setState({ segments: [seg('old')] });

    scheduleContentUpdate('old', 'old invocation content');
    useStreamStore.setState({ segments: [seg('old'), seg('new')] });
    flush?.(0);

    const segments = useStreamStore.getState().segments;
    expect(segments[0].content).toBe('old invocation content');
    expect(segments[1].content).toBe('');
  });

  describe('pending injects', () => {
    test('addPendingInject snapshots content at the current flow tail', () => {
      useStreamStore.setState({ segments: [seg('s1'), seg('s2')] });

      const id = useStreamStore.getState().addPendingInject('please adjust');

      const pending = useStreamStore.getState().pendingInjects;
      expect(pending).toHaveLength(1);
      expect(pending[0]).toMatchObject({
        kind: 'pending_inject',
        id,
        content: 'please adjust',
        position: 2,
      });
    });

    test('confirmPendingInject clears the matching pending inject', () => {
      const first = useStreamStore.getState().addPendingInject('first');
      const second = useStreamStore.getState().addPendingInject('second');

      useStreamStore.getState().confirmPendingInject('second');

      expect(useStreamStore.getState().pendingInjects.map((p) => p.id)).toEqual([first]);
      expect(useStreamStore.getState().pendingInjects.some((p) => p.id === second)).toBe(false);
    });

    test('confirmPendingInject leaves pending injects untouched when content does not match', () => {
      const first = useStreamStore.getState().addPendingInject('first');
      const second = useStreamStore.getState().addPendingInject('second');

      useStreamStore.getState().confirmPendingInject('from replay or another tab');

      expect(useStreamStore.getState().pendingInjects.map((p) => p.id)).toEqual([first, second]);
    });

    test('removePendingInject clears a failed POST without touching later pending injects', () => {
      const failed = useStreamStore.getState().addPendingInject('failed');
      const later = useStreamStore.getState().addPendingInject('later');

      useStreamStore.getState().removePendingInject(failed);

      expect(useStreamStore.getState().pendingInjects.map((p) => p.id)).toEqual([later]);
    });

    test('snapshotSegments does not persist local-only pending injects', () => {
      useStreamStore.setState({
        segments: [seg('s1', { reasoningContent: 'r' })],
      });
      useStreamStore.getState().addPendingInject('not persisted yet');

      useStreamStore.getState().snapshotSegments('msg-pending');

      const blockSnap = useStreamStore.getState().completedNonAgentBlocks.get('msg-pending');
      expect(blockSnap).toBeUndefined();
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

  describe('tool-call progress transition', () => {
    test('TOOL_START replacement removes only the matching queued draft', () => {
      useStreamStore.setState({
        segments: [seg('s1', {
          agent: 'lead',
          toolCallProgress: [
            { index: 0, callId: 'call-a', toolName: 'a', argumentsChars: 2, status: 'queued' },
            { index: 1, callId: 'call-b', toolName: 'b', argumentsChars: 4, status: 'queued' },
          ],
        })],
      });

      useStreamStore.getState().addToolCallToSegment({
        id: 'call-a', toolName: 'a', params: {}, agent: 'lead', status: 'running',
      });

      const segment = useStreamStore.getState().segments[0];
      expect(segment.toolCalls.map((call) => call.id)).toEqual(['call-a']);
      expect(segment.toolCallProgress.map((progress) => progress.callId)).toEqual(['call-b']);
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

    test('rebases compaction before retry when an empty overflow attempt is filtered', () => {
      useStreamStore.setState({
        segments: [
          seg('overflow-attempt'),
          seg('retry', { reasoningContent: 'retry thinking' }),
        ],
        nonAgentBlocks: [
          { ...compaction('overflow-compact', 1), reason: 'overflow' },
        ],
      });

      useStreamStore.getState().snapshotSegments('msg-overflow');

      const segments = useStreamStore.getState().completedSegments.get('msg-overflow')!;
      const blocks = useStreamStore.getState().completedNonAgentBlocks.get('msg-overflow')!;
      expect(blocks[0].position).toBe(0);
      expect(interleaveFlowItems(segments, blocks).map((item) => item.kind)).toEqual([
        'compaction',
        'agent',
      ]);
    });

    test('does not cache SSE-only tool-call progress', () => {
      useStreamStore.setState({
        segments: [seg('s1', {
          reasoningContent: 'thinking',
          toolCallProgress: [{
            index: 0,
            callId: 'call-draft',
            toolName: 'draft',
            argumentsChars: 99,
            status: 'generating',
          }],
        })],
      });

      useStreamStore.getState().snapshotSegments('msg-progress');

      const snap = useStreamStore.getState().completedSegments.get('msg-progress');
      expect(snap?.[0].toolCallProgress).toEqual([]);
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
