import { describe, test, expect, beforeEach } from 'vitest';
import { reconstructSegments, reconstructNonAgentBlocks } from './reconstructSegments';
import { makeEvent, resetEventSeq } from '@/test-utils/events';

describe('reconstructSegments', () => {
  beforeEach(() => resetEventSeq());

  test('empty events → empty array', () => {
    expect(reconstructSegments([])).toEqual([]);
  });

  test('agent_start with no tools and no reasoning → filtered out', () => {
    // llm_complete content alone (no tool_call marker, no reasoning) doesn't
    // make a segment "meaningful" — the final filter drops it.
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('llm_complete', { content: 'plain text' }, 'lead'),
      makeEvent('agent_complete', {}, 'lead'),
    ];
    expect(reconstructSegments(events)).toEqual([]);
  });

  test('agent_start + llm_complete with reasoning → segment kept', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('llm_complete', { reasoning_content: 'thinking...' }, 'lead'),
      makeEvent('agent_complete', {}, 'lead'),
    ];
    const segs = reconstructSegments(events);
    expect(segs).toHaveLength(1);
    expect(segs[0].agent).toBe('lead');
    expect(segs[0].reasoningContent).toBe('thinking...');
    expect(segs[0].isThinking).toBe(false);
    expect(segs[0].status).toBe('complete');
  });

  test('tool_start + tool_complete pair → tool call success', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('tool_start', { tool: 'search', params: { q: 'foo' } }, 'lead'),
      makeEvent('tool_complete', { tool: 'search', success: true, result_data: '{"hits":1}', duration_ms: 42 }, 'lead'),
      makeEvent('agent_complete', {}, 'lead'),
    ];
    const segs = reconstructSegments(events);
    expect(segs).toHaveLength(1);
    expect(segs[0].toolCalls).toHaveLength(1);
    expect(segs[0].toolCalls[0]).toMatchObject({
      toolName: 'search',
      status: 'success',
      result: '{"hits":1}',
      durationMs: 42,
    });
  });

  test('tool_complete success=false → status=error, result from data.error', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('tool_start', { tool: 'search' }, 'lead'),
      makeEvent('tool_complete', { tool: 'search', success: false, error: 'timeout' }, 'lead'),
    ];
    const segs = reconstructSegments(events);
    expect(segs[0].toolCalls[0].status).toBe('error');
    expect(segs[0].toolCalls[0].result).toBe('timeout');
  });

  test('llm_complete content with <tool_call> preserves to llmOutput', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('llm_complete', { content: 'I will <tool_call>do_thing</tool_call>' }, 'lead'),
      makeEvent('tool_start', { tool: 'do_thing' }, 'lead'),
      makeEvent('tool_complete', { tool: 'do_thing', success: true, result_data: 'done' }, 'lead'),
    ];
    const segs = reconstructSegments(events);
    expect(segs[0].llmOutput).toBe('I will <tool_call>do_thing</tool_call>');
    // content cleared by tool_start
    expect(segs[0].content).toBe('');
  });

  test('tokenUsage / model / duration_ms fields attached', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('llm_complete', {
        reasoning_content: 'x',
        token_usage: { input_tokens: 10, output_tokens: 5 },
        model: 'claude-opus-4-7',
        duration_ms: 1234,
      }, 'lead'),
    ];
    const segs = reconstructSegments(events);
    expect(segs[0].tokenUsage).toEqual({ input_tokens: 10, output_tokens: 5 });
    expect(segs[0].model).toBe('claude-opus-4-7');
    expect(segs[0].llmDurationMs).toBe(1234);
  });

  test('segment still running at end → forced to complete', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('tool_start', { tool: 'search' }, 'lead'),
      makeEvent('tool_complete', { tool: 'search', success: true, result_data: 'x' }, 'lead'),
      // no agent_complete
    ];
    const segs = reconstructSegments(events);
    expect(segs[0].status).toBe('complete');
  });

  test('two consecutive agent_start → two segments', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('tool_start', { tool: 'a' }, 'lead'),
      makeEvent('tool_complete', { tool: 'a', success: true, result_data: 'x' }, 'lead'),
      makeEvent('agent_complete', {}, 'lead'),
      makeEvent('agent_start', {}, 'search'),
      makeEvent('tool_start', { tool: 'b' }, 'search'),
      makeEvent('tool_complete', { tool: 'b', success: true, result_data: 'y' }, 'search'),
    ];
    const segs = reconstructSegments(events);
    expect(segs).toHaveLength(2);
    expect(segs[0].agent).toBe('lead');
    expect(segs[1].agent).toBe('search');
  });

  test('tool_complete searches across all segments for matching running tool', () => {
    // tool_start in seg 0, then a new agent_start opens seg 1, then tool_complete
    // arrives. The matching tool is in seg 0 — it should be found and finalized.
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('tool_start', { tool: 'slow_op' }, 'lead'),
      makeEvent('agent_start', {}, 'search'),
      makeEvent('tool_complete', { tool: 'slow_op', success: true, result_data: 'done' }, 'lead'),
    ];
    const segs = reconstructSegments(events);
    // Both segments may exist (search seg has no toolcalls/reasoning so filtered)
    const lead = segs.find(s => s.agent === 'lead')!;
    expect(lead.toolCalls[0].status).toBe('success');
    expect(lead.toolCalls[0].result).toBe('done');
  });
});

describe('reconstructNonAgentBlocks', () => {
  beforeEach(() => resetEventSeq());

  test('empty events → empty array', () => {
    expect(reconstructNonAgentBlocks([])).toEqual([]);
  });

  test('queued_message → InjectBlock with position=agentSegmentCount', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('queued_message', { content: 'wait, also do X' }),
    ];
    const blocks = reconstructNonAgentBlocks(events);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({
      kind: 'inject',
      content: 'wait, also do X',
      position: 1,
    });
  });

  test('queued_message before any agent_start → position=0', () => {
    const blocks = reconstructNonAgentBlocks([
      makeEvent('queued_message', { content: 'preamble' }),
    ]);
    expect(blocks[0].position).toBe(0);
  });

  test('compaction_start alone → state=running, no summary', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('compaction_start', { last_input_tokens: 50000, last_output_tokens: 1000 }),
    ];
    const blocks = reconstructNonAgentBlocks(events);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({
      kind: 'compaction',
      state: 'running',
      triggerTokens: { input: 50000, output: 1000 },
      position: 1,
    });
    expect((blocks[0] as { summary?: string }).summary).toBeUndefined();
  });

  test('compaction_start + compaction_summary → state=done, summary populated', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('compaction_start', { last_input_tokens: 50000, last_output_tokens: 1000 }),
      makeEvent('compaction_summary', {
        content: 'compacted summary text',
        model: 'claude-haiku-4-5',
        token_usage: { input_tokens: 50000, output_tokens: 200 },
        duration_ms: 1500,
      }),
    ];
    const blocks = reconstructNonAgentBlocks(events);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({
      kind: 'compaction',
      state: 'done',
      summary: 'compacted summary text',
      model: 'claude-haiku-4-5',
      tokenUsage: { input_tokens: 50000, output_tokens: 200 },
      durationMs: 1500,
      error: null,
    });
  });

  test('compaction_summary with error → state=error, error field populated', () => {
    const events = [
      makeEvent('compaction_start', {}),
      makeEvent('compaction_summary', { content: '', error: 'compact LLM failed' }),
    ];
    const blocks = reconstructNonAgentBlocks(events);
    expect(blocks[0]).toMatchObject({
      kind: 'compaction',
      state: 'error',
      error: 'compact LLM failed',
    });
  });

  test('orphan compaction_start (no summary) stays running', () => {
    const blocks = reconstructNonAgentBlocks([
      makeEvent('compaction_start', {}),
    ]);
    expect((blocks[0] as { state: string }).state).toBe('running');
  });

  test('compaction_summary pairs with most recent running block (right-to-left scan)', () => {
    // Two compactions: first done, second running. A new summary should pair
    // with the second (most recent running), not the first.
    const events = [
      makeEvent('compaction_start', {}),
      makeEvent('compaction_summary', { content: 'first summary' }),
      makeEvent('compaction_start', {}),
      makeEvent('compaction_summary', { content: 'second summary' }),
    ];
    const blocks = reconstructNonAgentBlocks(events);
    expect(blocks).toHaveLength(2);
    expect((blocks[0] as { summary: string }).summary).toBe('first summary');
    expect((blocks[1] as { summary: string }).summary).toBe('second summary');
  });

  test('inject + compaction interleaved → both kinds preserved', () => {
    const events = [
      makeEvent('agent_start', {}, 'lead'),
      makeEvent('queued_message', { content: 'inject1' }),
      makeEvent('compaction_start', {}),
      makeEvent('compaction_summary', { content: 'summary' }),
      makeEvent('queued_message', { content: 'inject2' }),
    ];
    const blocks = reconstructNonAgentBlocks(events);
    expect(blocks).toHaveLength(3);
    expect(blocks.map(b => b.kind)).toEqual(['inject', 'compaction', 'inject']);
  });
});
