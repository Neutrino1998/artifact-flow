import { describe, test, expect, beforeEach } from 'vitest';
import { useStagedFilesStore } from './stagedFilesStore';
import { MAX_CHAT_ATTACHMENTS } from '@/lib/constants';

function reset() {
  useStagedFilesStore.setState({ files: [], notice: null });
}

function makeFiles(n: number): File[] {
  return Array.from({ length: n }, (_, i) => new File(['x'], `f${i}.txt`, { type: 'text/plain' }));
}

describe('stagedFilesStore attachment cap', () => {
  beforeEach(() => reset());

  test('addFiles caps total at MAX_CHAT_ATTACHMENTS', () => {
    useStagedFilesStore.getState().addFiles(makeFiles(MAX_CHAT_ATTACHMENTS + 5));
    expect(useStagedFilesStore.getState().files.length).toBe(MAX_CHAT_ATTACHMENTS);
  });

  test('addFiles only fills the remaining room across batches', () => {
    const add = useStagedFilesStore.getState().addFiles;
    add(makeFiles(MAX_CHAT_ATTACHMENTS - 2));
    add(makeFiles(5)); // only 2 slots left
    expect(useStagedFilesStore.getState().files.length).toBe(MAX_CHAT_ATTACHMENTS);
  });

  test('addFiles is a no-op once at the cap', () => {
    const add = useStagedFilesStore.getState().addFiles;
    add(makeFiles(MAX_CHAT_ATTACHMENTS));
    add(makeFiles(3));
    expect(useStagedFilesStore.getState().files.length).toBe(MAX_CHAT_ATTACHMENTS);
  });

  test('removeFile and clear work', () => {
    useStagedFilesStore.getState().addFiles(makeFiles(3));
    const firstId = useStagedFilesStore.getState().files[0].id;
    useStagedFilesStore.getState().removeFile(firstId);
    expect(useStagedFilesStore.getState().files.length).toBe(2);
    useStagedFilesStore.getState().clear();
    expect(useStagedFilesStore.getState().files.length).toBe(0);
  });

  test('removeFiles removes only the given ids, preserving the rest', () => {
    useStagedFilesStore.getState().addFiles(makeFiles(3));
    const ids = useStagedFilesStore.getState().files.map((f) => f.id);
    // Remove the first two (simulating "the files a send consumed"); the third
    // (e.g. staged during the in-flight window) must survive.
    useStagedFilesStore.getState().removeFiles([ids[0], ids[1]]);
    const remaining = useStagedFilesStore.getState().files;
    expect(remaining.map((f) => f.id)).toEqual([ids[2]]);
  });
});

describe('stagedFilesStore format gate + notice', () => {
  beforeEach(() => reset());

  test('rejects unsupported office files by extension and records a per-file notice', () => {
    useStagedFilesStore.getState().addFiles([
      new File(['x'], 'a.txt', { type: 'text/plain' }),
      new File(['x'], 'b.doc'),
      new File(['x'], 'c.xlsx'),
    ]);
    const st = useStagedFilesStore.getState();
    expect(st.files.map((f) => f.file.name)).toEqual(['a.txt']);
    expect(st.notice?.rejected.map((r) => r.name)).toEqual(['b.doc', 'c.xlsx']);
    expect(st.notice?.overflow).toBe(0);
  });

  test('over-cap files (e.g. dropped past the disabled button) are reported as overflow', () => {
    useStagedFilesStore.getState().addFiles(makeFiles(MAX_CHAT_ATTACHMENTS + 3));
    const st = useStagedFilesStore.getState();
    expect(st.files.length).toBe(MAX_CHAT_ATTACHMENTS);
    expect(st.notice?.overflow).toBe(3);
    expect(st.notice?.rejected).toEqual([]);
  });

  test('a fully-clean add clears a prior notice', () => {
    useStagedFilesStore.getState().addFiles([new File(['x'], 'bad.doc')]);
    expect(useStagedFilesStore.getState().notice).not.toBeNull();
    useStagedFilesStore.getState().addFiles(makeFiles(1));
    expect(useStagedFilesStore.getState().notice).toBeNull();
  });

  test('dismissNotice clears the notice without touching staged files', () => {
    useStagedFilesStore.getState().addFiles([
      new File(['x'], 'ok.txt', { type: 'text/plain' }),
      new File(['x'], 'bad.doc'),
    ]);
    expect(useStagedFilesStore.getState().notice).not.toBeNull();
    useStagedFilesStore.getState().dismissNotice();
    expect(useStagedFilesStore.getState().notice).toBeNull();
    expect(useStagedFilesStore.getState().files.length).toBe(1);
  });
});
