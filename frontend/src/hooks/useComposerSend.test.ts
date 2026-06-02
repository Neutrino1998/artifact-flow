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
    content: 'hello',
    staged: [] as StagedFile[],
    setContent: vi.fn(),
    markSent: vi.fn(),
    lockRef: { current: false },
    setSending: vi.fn(),
    run: vi.fn(async () => true),
    ...over,
  };
}

describe('runComposerOp', () => {
  test('bails (no run, no lock, no spinner) when text and files are both empty', async () => {
    const d = deps({ content: '   ', staged: [] });
    await runComposerOp(d);
    expect(d.run).not.toHaveBeenCalled();
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

  test('on success, reconciles instead of blind-clearing (the R4 fix)', async () => {
    const d = deps({ content: 'hello', staged: makeStaged(2), run: vi.fn(async () => true) });
    await runComposerOp(d);
    // text reconcile is a functional updater, not a blind setContent('')
    expect(d.setContent).toHaveBeenCalledTimes(1);
    const updater = vi.mocked(d.setContent).mock.calls[0][0] as (prev: string) => string;
    expect(updater('hello')).toBe('');                 // unchanged → cleared
    expect(updater('hello, more typed since')).toBe('hello, more typed since'); // changed → kept
    // only the ids this send consumed are removed
    expect(d.markSent).toHaveBeenCalledWith(['s0', 's1']);
  });

  test('does not call markSent when nothing was staged', async () => {
    const d = deps({ content: 'hello', staged: [] });
    await runComposerOp(d);
    expect(d.markSent).not.toHaveBeenCalled();
  });

  test('on failure (run returns false), preserves text and files', async () => {
    const d = deps({ content: 'hello', staged: makeStaged(1), run: vi.fn(async () => false) });
    await runComposerOp(d);
    expect(d.setContent).not.toHaveBeenCalled();
    expect(d.markSent).not.toHaveBeenCalled();
  });

  test('on throw, preserves composer and still releases the lock', async () => {
    const d = deps({
      content: 'hello',
      staged: makeStaged(1),
      run: vi.fn(async () => {
        throw new Error('network');
      }),
    });
    await runComposerOp(d);
    expect(d.setContent).not.toHaveBeenCalled();
    expect(d.markSent).not.toHaveBeenCalled();
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
