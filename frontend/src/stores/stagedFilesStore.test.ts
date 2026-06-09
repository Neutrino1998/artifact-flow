import { describe, test, expect, beforeEach, afterEach } from 'vitest';
import { useStagedFilesStore, NEW_DRAFT_KEY } from './stagedFilesStore';
import { useConfigStore } from './configStore';
import { MAX_CHAT_ATTACHMENTS } from '@/lib/constants';

function reset() {
  useStagedFilesStore.setState({
    text: '',
    files: [],
    notice: null,
    activeKey: NEW_DRAFT_KEY,
    archive: {},
  });
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

  test('removeFile drops the given file', () => {
    useStagedFilesStore.getState().addFiles(makeFiles(3));
    const firstId = useStagedFilesStore.getState().files[0].id;
    useStagedFilesStore.getState().removeFile(firstId);
    expect(useStagedFilesStore.getState().files.length).toBe(2);
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

describe('stagedFilesStore filename dedup (mirror backend _N)', () => {
  beforeEach(() => reset());

  test('same-name files in one batch get distinct names', () => {
    useStagedFilesStore.getState().addFiles([
      new File(['x'], 'a.png', { type: 'image/png' }),
      new File(['y'], 'a.png', { type: 'image/png' }),
      new File(['z'], 'a.png', { type: 'image/png' }),
    ]);
    expect(useStagedFilesStore.getState().files.map((f) => f.file.name)).toEqual([
      'a.png',
      'a_1.png',
      'a_2.png',
    ]);
  });

  test('dedup spans batches (collision with already-staged file)', () => {
    const add = useStagedFilesStore.getState().addFiles;
    add([new File(['x'], 'shot.png', { type: 'image/png' })]);
    add([new File(['y'], 'shot.png', { type: 'image/png' })]);
    expect(useStagedFilesStore.getState().files.map((f) => f.file.name)).toEqual([
      'shot.png',
      'shot_1.png',
    ]);
  });

  test('extensionless names dedup too; distinct names untouched', () => {
    useStagedFilesStore.getState().addFiles([
      new File(['x'], 'README', { type: 'text/plain' }),
      new File(['y'], 'README', { type: 'text/plain' }),
      new File(['z'], 'notes.txt', { type: 'text/plain' }),
    ]);
    expect(useStagedFilesStore.getState().files.map((f) => f.file.name)).toEqual([
      'README',
      'README_1',
      'notes.txt',
    ]);
  });

  test('renamed clone preserves type (so image detection still works)', () => {
    useStagedFilesStore.getState().addFiles([
      new File(['x'], 'a.png', { type: 'image/png' }),
      new File(['y'], 'a.png', { type: 'image/png' }),
    ]);
    const second = useStagedFilesStore.getState().files[1].file;
    expect(second.name).toBe('a_1.png');
    expect(second.type).toBe('image/png');
  });
});

describe('stagedFilesStore per-file size gate (mirrors backend MAX_UPLOAD_SIZE via /meta)', () => {
  beforeEach(() => reset());
  afterEach(() => useConfigStore.setState({ maxUploadSize: null }));

  test('rejects an over-limit file and records a size notice; under-limit stages', () => {
    useConfigStore.setState({ maxUploadSize: 4 }); // 4-byte limit
    useStagedFilesStore.getState().addFiles([
      new File(['ab'], 'small.txt', { type: 'text/plain' }),     // 2B → ok
      new File(['abcdef'], 'big.txt', { type: 'text/plain' }),   // 6B → over
    ]);
    const st = useStagedFilesStore.getState();
    expect(st.files.map((f) => f.file.name)).toEqual(['small.txt']);
    expect(st.notice?.rejected.map((r) => r.name)).toEqual(['big.txt']);
    expect(st.notice?.rejected[0].reason).toContain('文件过大');
  });

  test('no limit fetched (null) → size gate skipped, oversize stages', () => {
    // maxUploadSize stays null (afterEach default) — best-effort: don't block on
    // a value the meta fetch hasn't delivered; the backend 422s if truly over.
    useStagedFilesStore.getState().addFiles([
      new File(['abcdef'], 'big.txt', { type: 'text/plain' }),
    ]);
    expect(useStagedFilesStore.getState().files.map((f) => f.file.name)).toEqual(['big.txt']);
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

describe('stagedFilesStore sent lifecycle (keep-until-COMPLETE backstop)', () => {
  beforeEach(reset);

  function stage(n: number): string[] {
    useStagedFilesStore.getState().addFiles(
      Array.from({ length: n }, (_, i) => new File(['x'], `f${i}.md`, { type: 'text/markdown' }))
    );
    return useStagedFilesStore.getState().files.map((f) => f.id);
  }

  test('markSent flags only the sent ids; files stay visible', () => {
    const ids = stage(2);
    useStagedFilesStore.getState().markSent([ids[0]]);
    const files = useStagedFilesStore.getState().files;
    expect(files.length).toBe(2);                 // not removed
    expect(files.find((f) => f.id === ids[0])?.sent).toBe(true);
    expect(files.find((f) => f.id === ids[1])?.sent).toBeFalsy();
  });

  test('clearSent (COMPLETE) drops only sent files, keeps newly-staged', () => {
    const ids = stage(1);
    useStagedFilesStore.getState().markSent(ids);
    stage(1); // a file staged during the in-flight window (sent=false)
    useStagedFilesStore.getState().clearSent();
    const files = useStagedFilesStore.getState().files;
    expect(files.length).toBe(1);
    expect(files[0].sent).toBeFalsy();
  });

  test('unmarkSent (cancel/error/timeout) reverts sent → staged for retry', () => {
    const ids = stage(2);
    useStagedFilesStore.getState().markSent(ids);
    useStagedFilesStore.getState().unmarkSent();
    const files = useStagedFilesStore.getState().files;
    expect(files.length).toBe(2);                 // kept, retryable
    expect(files.every((f) => !f.sent)).toBe(true);
  });
});

describe('stagedFilesStore per-conversation drafts (in-memory)', () => {
  beforeEach(reset);
  const s = () => useStagedFilesStore.getState();

  test('activate stashes the live draft and loads the target conversation’s', () => {
    s().setText('draft for new chat');
    s().addFiles(makeFiles(1));
    s().activate('conv-a');                 // switch away from the new chat
    expect(s().activeKey).toBe('conv-a');
    expect(s().text).toBe('');              // conv-a starts blank
    expect(s().files.length).toBe(0);
    s().setText('hello A');
    s().activate(NEW_DRAFT_KEY);            // back to the new chat
    expect(s().text).toBe('draft for new chat');
    expect(s().files.length).toBe(1);
    s().activate('conv-a');                 // conv-a's draft survived too
    expect(s().text).toBe('hello A');
  });

  test('activate is a no-op when the key equals the active key (keeps draft)', () => {
    s().setText('keep me');
    s().activate(NEW_DRAFT_KEY);            // already the active key
    expect(s().text).toBe('keep me');
  });

  test('a blank live slot is not archived (no stale blank drafts accumulate)', () => {
    s().activate('conv-a');                 // leave the (blank) new chat
    s().setText('A');
    s().activate(NEW_DRAFT_KEY);            // back: new chat had nothing to restore
    expect(s().text).toBe('');
    expect(s().archive[NEW_DRAFT_KEY]).toBeUndefined();
  });

  test('activate archives only unsent files; in-flight (sent) ones are dropped', () => {
    s().addFiles(makeFiles(2));
    const ids = s().files.map((f) => f.id);
    s().markSent([ids[0]]);                 // one rode an in-flight send
    s().activate('conv-a');
    s().activate(NEW_DRAFT_KEY);            // come back
    const files = s().files;
    expect(files.length).toBe(1);           // only the unsent file was archived
    expect(files[0].id).toBe(ids[1]);
  });

  test('promoteNewDraft relabels the live slot so the draft does not leak into the next new chat', () => {
    s().setText('first-turn follow-up');
    s().promoteNewDraft('conv-x');          // new conv landed its real id
    expect(s().activeKey).toBe('conv-x');
    expect(s().text).toBe('first-turn follow-up'); // live slot text untouched
    s().activate('conv-b');
    s().activate(NEW_DRAFT_KEY);            // a fresh new chat is blank...
    expect(s().text).toBe('');
    s().activate('conv-x');                 // ...and the draft is under conv-x
    expect(s().text).toBe('first-turn follow-up');
  });

  test('promoteNewDraft is a no-op for an existing conversation', () => {
    s().activate('conv-a');                 // activeKey is a real id, not the sentinel
    s().promoteNewDraft('conv-z');
    expect(s().activeKey).toBe('conv-a');
  });
});
