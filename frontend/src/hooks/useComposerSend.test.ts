import { describe, test, expect, vi } from 'vitest';
import { runComposerOp } from './useComposerSend';
import type { StagedFile } from '@/stores/stagedFilesStore';

function makeStaged(n: number): StagedFile[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `s${i}`,
    file: new File(['x'], `f${i}.txt`, { type: 'text/plain' }),
  }));
}

// Minimal deps with vi.fn() spies; override per test.
function deps(over: Partial<Parameters<typeof runComposerOp>[0]> = {}) {
  return {
    ownerKey: 'conv-1',
    content: 'hello',
    staged: [] as StagedFile[],
    claimSend: vi.fn(),
    restoreSend: vi.fn(),
    lockRef: { current: false },
    setSending: vi.fn(),
    run: vi.fn(async () => true),
    ...over,
  };
}

describe('runComposerOp', () => {
  test('bails (no run, no claim, no lock, no spinner) when text and files are both empty', async () => {
    const d = deps({ content: '   ', staged: [] });
    await runComposerOp(d);
    expect(d.run).not.toHaveBeenCalled();
    expect(d.claimSend).not.toHaveBeenCalled();
    expect(d.setSending).not.toHaveBeenCalled();
    expect(d.lockRef.current).toBe(false);
  });

  test('allows files-only send (empty text + staged attachments)', async () => {
    const d = deps({ content: '', staged: makeStaged(2) });
    await runComposerOp(d);
    expect(d.run).toHaveBeenCalledTimes(1);
    // run receives the trimmed text ('') and the File[] (length 2)
    const [text, files] = vi.mocked(d.run).mock.calls[0];
    expect(text).toBe('');
    expect(files).toHaveLength(2);
  });

  test('claims the OWNER draft BEFORE the await (clear text + mark files sent)', async () => {
    const claimSend = vi.fn();
    let claimedBeforeRun = false;
    const run = vi.fn(async () => {
      claimedBeforeRun = claimSend.mock.calls.length > 0;
      return true;
    });
    const d = deps({ ownerKey: 'conv-9', content: 'hello', staged: makeStaged(2), claimSend, run });
    await runComposerOp(d);
    expect(claimedBeforeRun).toBe(true);
    expect(claimSend).toHaveBeenCalledWith('conv-9', 'hello', ['s0', 's1']);
  });

  test('on success, does not restore (the content already left the draft at claim)', async () => {
    const d = deps({ content: 'hello', staged: makeStaged(1), run: vi.fn(async () => true) });
    await runComposerOp(d);
    expect(d.claimSend).toHaveBeenCalledTimes(1);
    expect(d.restoreSend).not.toHaveBeenCalled();
  });

  test('on failure (run returns false), restores the OWNER draft', async () => {
    const d = deps({ ownerKey: 'conv-9', content: 'hello', staged: makeStaged(1), run: vi.fn(async () => false) });
    await runComposerOp(d);
    expect(d.restoreSend).toHaveBeenCalledWith('conv-9', 'hello', ['s0']);
  });

  test('on throw, restores the OWNER draft and still releases the lock', async () => {
    const d = deps({
      ownerKey: 'conv-9',
      content: 'hello',
      staged: makeStaged(1),
      run: vi.fn(async () => {
        throw new Error('network');
      }),
    });
    await runComposerOp(d);
    expect(d.restoreSend).toHaveBeenCalledWith('conv-9', 'hello', ['s0']);
    expect(d.lockRef.current).toBe(false);
    expect(d.setSending).toHaveBeenLastCalledWith(false);
  });

  test('re-entrancy: a second call while one is in flight is dropped (the R3 fix)', async () => {
    let resolveRun: (v: boolean) => void = () => {};
    const run = vi.fn(() => new Promise<boolean>((res) => (resolveRun = res)));
    const lockRef = { current: false };
    const shared = deps({ run, lockRef });

    const p1 = runComposerOp(shared);
    expect(lockRef.current).toBe(true); // first call holds the lock

    await runComposerOp(deps({ run, lockRef })); // second call sees the lock → bails
    expect(run).toHaveBeenCalledTimes(1);

    resolveRun(true);
    await p1;
    expect(lockRef.current).toBe(false); // released for the next send
  });

  test('toggles the optional spinner around the await', async () => {
    const d = deps();
    await runComposerOp(d);
    expect(d.setSending).toHaveBeenNthCalledWith(1, true);
    expect(d.setSending).toHaveBeenLastCalledWith(false);
  });
});
