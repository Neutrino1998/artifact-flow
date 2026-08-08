import { beforeEach, describe, expect, test } from 'vitest';
import { bumpNavGen, getNavGen, _resetNavGenForTests } from '@/lib/navGen';
import { useStreamStore } from '@/stores/streamStore';
import { isTerminalRefreshOwner } from './terminalRefreshOwnership';

describe('isTerminalRefreshOwner', () => {
  beforeEach(() => {
    _resetNavGenForTests();
    useStreamStore.getState().reset();
  });

  test('completed turn keeps ownership while it remains the latest message', () => {
    useStreamStore.getState().startStream('/turn-1', 'message-1', 'conversation-1');
    useStreamStore.getState().endStream();

    expect(isTerminalRefreshOwner(getNavGen(), 'message-1')).toBe(true);
  });

  test('starting a new turn in the same conversation invalidates the old terminal', () => {
    useStreamStore.getState().startStream('/turn-1', 'message-1', 'conversation-1');
    useStreamStore.getState().endStream();
    const capturedNavGen = getNavGen();

    useStreamStore.getState().startStream('/turn-2', 'message-2', 'conversation-1');

    expect(isTerminalRefreshOwner(capturedNavGen, 'message-1')).toBe(false);
  });

  test('navigation invalidates the terminal even when the message id is unchanged', () => {
    useStreamStore.getState().startStream('/turn-1', 'message-1', 'conversation-1');
    useStreamStore.getState().endStream();
    const capturedNavGen = getNavGen();

    bumpNavGen();

    expect(isTerminalRefreshOwner(capturedNavGen, 'message-1')).toBe(false);
  });
});
