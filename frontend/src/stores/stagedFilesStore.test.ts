import { describe, test, expect, beforeEach, afterEach } from 'vitest';
import { useStagedFilesStore, NEW_DRAFT_KEY } from './stagedFilesStore';
import { useConfigStore } from './configStore';
import { MAX_CHAT_ATTACHMENTS } from '@/lib/constants';

// A real-conversation-like active key (not a 'new:' temp key) for the common
// tests; promote/new-chat tests allocate temp keys via startNewDraft().
const KEY = 'conv-test';

function reset() {
  useStagedFilesStore.setState({ drafts: {}, activeKey: KEY, notice: null });
}

const st = () => useStagedFilesStore.getState();
const files = () => st().drafts[st().activeKey]?.files ?? [];
const text = () => st().drafts[st().activeKey]?.text ?? '';

function makeFiles(n: number): File[] {
  return Array.from({ length: n }, (_, i) => new File(['x'], `f${i}.txt`, { type: 'text/plain' }));
}

describe('stagedFilesStore attachment cap', () => {
  beforeEach(() => reset());

  test('addFiles caps total at MAX_CHAT_ATTACHMENTS', () => {
    st().addFiles(makeFiles(MAX_CHAT_ATTACHMENTS + 5));
    expect(files().length).toBe(MAX_CHAT_ATTACHMENTS);
  });

  test('addFiles only fills the remaining room across batches', () => {
    st().addFiles(makeFiles(MAX_CHAT_ATTACHMENTS - 2));
    st().addFiles(makeFiles(5)); // only 2 slots left
    expect(files().length).toBe(MAX_CHAT_ATTACHMENTS);
  });

  test('addFiles is a no-op once at the cap', () => {
    st().addFiles(makeFiles(MAX_CHAT_ATTACHMENTS));
    st().addFiles(makeFiles(3));
    expect(files().length).toBe(MAX_CHAT_ATTACHMENTS);
  });

  test('removeFile drops the given file', () => {
    st().addFiles(makeFiles(3));
    const firstId = files()[0].id;
    st().removeFile(firstId);
    expect(files().length).toBe(2);
  });
});

describe('stagedFilesStore filename dedup (mirror backend _N)', () => {
  beforeEach(() => reset());

  test('same-name files in one batch get distinct names', () => {
    st().addFiles([
      new File(['x'], 'a.png', { type: 'image/png' }),
      new File(['y'], 'a.png', { type: 'image/png' }),
      new File(['z'], 'a.png', { type: 'image/png' }),
    ]);
    expect(files().map((f) => f.file.name)).toEqual(['a.png', 'a_1.png', 'a_2.png']);
  });

  test('dedup spans batches (collision with already-staged file)', () => {
    st().addFiles([new File(['x'], 'shot.png', { type: 'image/png' })]);
    st().addFiles([new File(['y'], 'shot.png', { type: 'image/png' })]);
    expect(files().map((f) => f.file.name)).toEqual(['shot.png', 'shot_1.png']);
  });

  test('extensionless names dedup too; distinct names untouched', () => {
    st().addFiles([
      new File(['x'], 'README', { type: 'text/plain' }),
      new File(['y'], 'README', { type: 'text/plain' }),
      new File(['z'], 'notes.txt', { type: 'text/plain' }),
    ]);
    expect(files().map((f) => f.file.name)).toEqual(['README', 'README_1', 'notes.txt']);
  });

  test('renamed clone preserves type (so image detection still works)', () => {
    st().addFiles([
      new File(['x'], 'a.png', { type: 'image/png' }),
      new File(['y'], 'a.png', { type: 'image/png' }),
    ]);
    const second = files()[1].file;
    expect(second.name).toBe('a_1.png');
    expect(second.type).toBe('image/png');
  });
});

describe('stagedFilesStore per-file size gate (mirrors backend MAX_UPLOAD_SIZE via /meta)', () => {
  beforeEach(() => reset());
  afterEach(() => useConfigStore.setState({ maxUploadSize: null }));

  test('rejects an over-limit file and records a size notice; under-limit stages', () => {
    useConfigStore.setState({ maxUploadSize: 4 }); // 4-byte limit
    st().addFiles([
      new File(['ab'], 'small.txt', { type: 'text/plain' }),     // 2B → ok
      new File(['abcdef'], 'big.txt', { type: 'text/plain' }),   // 6B → over
    ]);
    expect(files().map((f) => f.file.name)).toEqual(['small.txt']);
    expect(st().notice?.rejected.map((r) => r.name)).toEqual(['big.txt']);
    expect(st().notice?.rejected[0].reason).toContain('文件过大');
  });

  test('no limit fetched (null) → size gate skipped, oversize stages', () => {
    st().addFiles([new File(['abcdef'], 'big.txt', { type: 'text/plain' })]);
    expect(files().map((f) => f.file.name)).toEqual(['big.txt']);
  });
});

describe('stagedFilesStore format gate + notice', () => {
  beforeEach(() => reset());

  test('rejects unsupported office files by extension and records a per-file notice', () => {
    st().addFiles([
      new File(['x'], 'a.txt', { type: 'text/plain' }),
      new File(['x'], 'b.doc'),
      new File(['x'], 'c.xlsx'),
    ]);
    expect(files().map((f) => f.file.name)).toEqual(['a.txt']);
    expect(st().notice?.rejected.map((r) => r.name)).toEqual(['b.doc', 'c.xlsx']);
    expect(st().notice?.overflow).toBe(0);
  });

  test('over-cap files (e.g. dropped past the disabled button) are reported as overflow', () => {
    st().addFiles(makeFiles(MAX_CHAT_ATTACHMENTS + 3));
    expect(files().length).toBe(MAX_CHAT_ATTACHMENTS);
    expect(st().notice?.overflow).toBe(3);
    expect(st().notice?.rejected).toEqual([]);
  });

  test('a fully-clean add clears a prior notice', () => {
    st().addFiles([new File(['x'], 'bad.doc')]);
    expect(st().notice).not.toBeNull();
    st().addFiles(makeFiles(1));
    expect(st().notice).toBeNull();
  });

  test('dismissNotice clears the notice without touching staged files', () => {
    st().addFiles([
      new File(['x'], 'ok.txt', { type: 'text/plain' }),
      new File(['x'], 'bad.doc'),
    ]);
    expect(st().notice).not.toBeNull();
    st().dismissNotice();
    expect(st().notice).toBeNull();
    expect(files().length).toBe(1);
  });
});

describe('stagedFilesStore send lifecycle (claim → terminal)', () => {
  beforeEach(reset);

  function stage(n: number): string[] {
    st().addFiles(
      Array.from({ length: n }, (_, i) => new File(['x'], `f${i}.md`, { type: 'text/markdown' }))
    );
    return files().map((f) => f.id);
  }

  test('claimSend clears the sent text and marks only the sent ids in-flight', () => {
    st().setText('hello');
    const ids = stage(2);
    st().claimSend(KEY, 'hello', [ids[0]]);
    expect(text()).toBe('');                                  // sent text cleared
    expect(files().length).toBe(2);                           // not removed
    expect(files().find((f) => f.id === ids[0])?.sent).toBe(true);
    expect(files().find((f) => f.id === ids[1])?.sent).toBeFalsy();
  });

  test('clearSent (flushed) drops only sent files, keeps newly-staged', () => {
    const ids = stage(1);
    st().claimSend(KEY, '', ids);
    stage(1); // staged during the in-flight window (sent=false)
    st().clearSent(KEY);
    expect(files().length).toBe(1);
    expect(files()[0].sent).toBeFalsy();
  });

  test('unmarkSent (not flushed) reverts sent → staged for retry', () => {
    const ids = stage(2);
    st().claimSend(KEY, '', ids);
    st().unmarkSent(KEY);
    expect(files().length).toBe(2);
    expect(files().every((f) => !f.sent)).toBe(true);
  });

  test('restoreSend (failed send) puts the text back and reverts the files', () => {
    st().setText('retry me');
    const ids = stage(1);
    st().claimSend(KEY, 'retry me', ids); // claim clears text + marks sent
    expect(text()).toBe('');
    st().restoreSend(KEY, 'retry me', ids);
    expect(text()).toBe('retry me');
    expect(files()[0].sent).toBeFalsy();
  });

  test('restoreSend does not clobber text typed during the in-flight window', () => {
    st().setText('original');
    st().claimSend(KEY, 'original', []); // text cleared at send start
    st().setText('typed during await'); // user types something new
    st().restoreSend(KEY, 'original', []);
    expect(text()).toBe('typed during await'); // new text wins, not restored
  });

  test('send ops are owner-keyed: claiming one conversation does not touch another', () => {
    st().setText('A draft');
    st().activate('conv-b');
    st().setText('B draft');
    // A send for conv-b is claimed while we sit on conv-b...
    st().claimSend('conv-b', 'B draft', []);
    // ...the (still-viewing) conv-b text is cleared, conv-a's archived draft is intact.
    expect(text()).toBe('');
    st().activate(KEY);
    expect(text()).toBe('A draft');
  });
});

describe('stagedFilesStore per-conversation drafts (in-memory)', () => {
  beforeEach(reset);

  test('each conversation keeps its own draft; switching back restores it', () => {
    st().setText('draft A');
    st().addFiles(makeFiles(1));
    st().activate('conv-b');
    expect(text()).toBe('');
    expect(files().length).toBe(0);
    st().setText('draft B');
    st().activate(KEY);
    expect(text()).toBe('draft A');
    expect(files().length).toBe(1);
    st().activate('conv-b');
    expect(text()).toBe('draft B');
  });

  test('a blank draft is pruned on leave, not kept as an empty entry', () => {
    st().activate('conv-b'); // leaving the blank active key
    expect(st().drafts[KEY]).toBeUndefined();
  });

  test('leaving a conversation drops its sent (in-flight) files, keeps unsent', () => {
    st().addFiles(makeFiles(2));
    const ids = files().map((f) => f.id);
    st().claimSend(KEY, '', [ids[0]]); // one file in-flight, one still staged
    st().activate('conv-b');
    st().activate(KEY);
    expect(files().length).toBe(1); // only the unsent file survived
    expect(files()[0].id).toBe(ids[1]);
  });

  test('the new-chat draft survives navigating away and clicking back into the new chat', () => {
    // The headline feature: there's one stable new-chat key, so an unsent draft
    // returns when the user clicks the new-chat button after glancing elsewhere.
    useStagedFilesStore.setState({ drafts: {}, activeKey: NEW_DRAFT_KEY, notice: null });
    st().setText('my new-chat draft');
    st().addFiles(makeFiles(1));
    st().activate('conv-a'); // glance at an existing conversation
    expect(text()).toBe('');
    st().startNewDraft(); // click "new chat" to return
    expect(st().activeKey).toBe(NEW_DRAFT_KEY);
    expect(text()).toBe('my new-chat draft');
    expect(files().length).toBe(1);
  });

  test('startNewDraft preserves the leaving conversation’s unsent draft', () => {
    st().setText('existing conv draft');
    const before = st().activeKey;
    st().startNewDraft();
    expect(st().activeKey).toBe(NEW_DRAFT_KEY);
    expect(text()).toBe('');
    st().activate(before);
    expect(text()).toBe('existing conv draft');
  });

  test('promoteNewDraft relabels the new-chat draft to its real id; next new chat is blank', () => {
    useStagedFilesStore.setState({ drafts: {}, activeKey: NEW_DRAFT_KEY, notice: null });
    st().setText('first-turn follow-up');
    st().promoteNewDraft('conv-x');
    expect(st().activeKey).toBe('conv-x');
    expect(text()).toBe('first-turn follow-up');
    expect(st().drafts[NEW_DRAFT_KEY]).toBeUndefined(); // sentinel freed
    st().startNewDraft(); // a fresh new chat...
    expect(st().activeKey).toBe(NEW_DRAFT_KEY);
    expect(text()).toBe(''); // ...is blank
    st().activate('conv-x'); // ...and the draft is under the real id
    expect(text()).toBe('first-turn follow-up');
  });

  test('promoteNewDraft is a no-op for an existing conversation', () => {
    st().activate('conv-a'); // a real id, not the sentinel
    st().promoteNewDraft('conv-z');
    expect(st().activeKey).toBe('conv-a');
  });

  test('a failed new-chat send restores its content into the new chat for retry', () => {
    // Success would promote the sentinel to a real id (freeing it); a failure
    // keeps the user on the same new chat, so restoring the content there is the
    // retry affordance — not a cross-conversation leak.
    useStagedFilesStore.setState({ drafts: {}, activeKey: NEW_DRAFT_KEY, notice: null });
    st().setText('hi');
    st().claimSend(NEW_DRAFT_KEY, 'hi', []); // claim at send start clears it
    expect(text()).toBe('');
    st().restoreSend(NEW_DRAFT_KEY, 'hi', []); // the POST failed
    expect(text()).toBe('hi'); // back in the new chat for retry
  });
});
