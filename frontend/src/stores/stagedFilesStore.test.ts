import { describe, test, expect, beforeEach } from 'vitest';
import { useStagedFilesStore } from './stagedFilesStore';
import { MAX_CHAT_ATTACHMENTS } from '@/lib/constants';

function reset() {
  useStagedFilesStore.setState({ files: [] });
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
