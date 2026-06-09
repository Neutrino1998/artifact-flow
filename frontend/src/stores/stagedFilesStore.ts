import { create } from 'zustand';
import { MAX_CHAT_ATTACHMENTS } from '@/lib/constants';
import { partitionStageable, type StageRejection } from '@/lib/uploadFilter';
import { useConfigStore } from '@/stores/configStore';

// The composer draft store: the unsent text + files staged in the composer
// (via the file button, drag-drop, or a huge paste). Files ride the next
// message: useChat.sendMessage posts them as multipart attachments to POST
// /chat, which converts each into a user_upload artifact before the turn
// starts. Shared store because drag-drop lives in ChatPanel while the button /
// paste / chips / text live in MessageInput.
//
// Per-conversation drafts (in-memory only): the live slot (`text` + `files`)
// holds the active conversation's draft; `archive` stashes other conversations'
// unsent drafts keyed by conversation id. `activate()` swaps the live slot on
// navigation (replacing the old clear()-on-switch), so an unsent draft survives
// glancing at another conversation and coming back. NOT persisted across reload
// — File bytes can't round-trip localStorage and the feature was scoped to
// in-page caching, so a reload starts every composer blank, by design.
//
// `text` also had to move OFF MessageInput's local useState into this store:
// switchConversation flips currentLoading, which unmounts MessageInput (the
// loading placeholder), so component-local state can't survive a switch. Store
// state can.

// Sentinel key for the not-yet-persisted "new conversation" composer. Real
// conversations key their draft by id; the new chat has no id until its first
// turn completes — at which point promoteNewDraft() relabels the live slot to
// the real id so the draft doesn't leak back into the next new chat.
export const NEW_DRAFT_KEY = '__new__';

export interface StagedFile {
  id: string;
  file: File;
  // True once this file has ridden a send POST but the turn hasn't reached a
  // terminal. Kept (not removed) until the terminal resolves so that if the turn
  // dies before flush_all — uploads are ephemeral (staged in-engine, lost on
  // lease restart) — the user still has the file in the composer to retry.
  // Resolution is driven by the terminal's `artifacts_flushed` bit, NOT the
  // terminal type (see useSSE.resolveStagedAfterTerminal): flushed → clearSent
  // (drop); not flushed → unmarkSent (revert to normal staged for retry).
  sent?: boolean;
}

// Why a file the user picked didn't make it into the staged set, surfaced once
// per addFiles batch so the user isn't left guessing. `rejected` = files the
// backend would 422 on by extension (see lib/uploadFilter); `overflow` = count
// dropped because the batch would exceed MAX_CHAT_ATTACHMENTS. The drag-drop
// path bypasses the disabled attach button, so the cap can be hit by a drop
// even when the button is locked — both paths funnel through addFiles, so the
// notice covers them uniformly. null = nothing to report (also clears a stale
// notice on the next clean add).
export interface StageNotice {
  rejected: StageRejection[];
  overflow: number;
}

// A single conversation's unsent composer draft, as archived while another
// conversation is active.
interface Draft {
  text: string;
  files: StagedFile[];
}

interface StagedFilesState {
  // Live slot — the active conversation's draft. Every existing action below
  // mutates this slot, unchanged; the per-conversation keying lives entirely in
  // `activate` / `archive`, so the file lifecycle (sent/markSent/…) is untouched.
  text: string;
  files: StagedFile[];
  notice: StageNotice | null;
  // The conversation key the live slot belongs to (NEW_DRAFT_KEY or a conv id).
  activeKey: string;
  // Other conversations' archived unsent drafts. In-memory only (see header).
  archive: Record<string, Draft>;
  setText: (text: string) => void;
  addFiles: (files: File[]) => void;
  removeFile: (id: string) => void;
  // Remove a specific set of ids — used to clear exactly the files that a send
  // consumed, preserving any the user staged during the in-flight window.
  removeFiles: (ids: string[]) => void;
  // Mark the ids a send just consumed as in-flight (sent=true). They stay
  // visible until the turn's terminal event resolves them (see below).
  markSent: (ids: string[]) => void;
  // Terminal with uploads flushed (COMPLETE, cooperative cancel, timeout,
  // engine error): drop the in-flight files.
  clearSent: () => void;
  // Terminal with uploads NOT flushed (staging abort, flush_error, external
  // cancel): revert in-flight files to normal staged so the user can retry.
  unmarkSent: () => void;
  dismissNotice: () => void;
  // Navigation hook (replaces the old clear()-on-switch): stash the live draft
  // under the current key and load `key`'s archived draft (or a blank one).
  activate: (key: string) => void;
  // A new conversation just got its real id: relabel the live slot so its draft
  // archives under the real id, not the shared NEW_DRAFT_KEY sentinel.
  promoteNewDraft: (id: string) => void;
}

let _seq = 0;
function nextId(): string {
  _seq += 1;
  return `staged-${Date.now()}-${_seq}`;
}

// Mirror the backend's `name_N.ext` dedup so two same-named files (e.g. an
// `a.png` dragged from two folders) get distinct names in the staged set. The
// uploaded filename is our correlation key: the backend echoes it verbatim as
// ARTIFACT_CREATED.original_filename (it only dedupes the artifact *id*), so the
// panel/chip match files by name — a name collision would mis-bind the preview
// to the wrong File until COMPLETE. This is decoupled from the backend's id
// dedup: we don't need identical strings, only uniqueness within the active set.
function uniqueFileName(name: string, used: Set<string>): string {
  if (!used.has(name)) return name;
  const dot = name.lastIndexOf('.');
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : '';
  let n = 1;
  while (used.has(`${stem}_${n}${ext}`)) n += 1;
  return `${stem}_${n}${ext}`;
}

export const useStagedFilesStore = create<StagedFilesState>((set) => ({
  text: '',
  files: [],
  notice: null,
  activeKey: NEW_DRAFT_KEY,
  archive: {},
  setText: (text) => set({ text }),
  // Gate then cap, in that order, so every entry point (button / drag-drop /
  // paste-to-stage) behaves identically:
  //   1. drop what the backend rejects on sight — unsupported extension OR a
  //      file over the per-file size limit (avoids a doomed 422 + the partial-
  //      batch orphan it could leave behind);
  //   2. cap the remainder at MAX_CHAT_ATTACHMENTS (the backend also 422s past
  //      this, but staging caps it so that's unreachable in normal use).
  // Anything dropped by either step is reported via `notice` (a drop that
  // bypasses the disabled button still surfaces here). `notice` is replaced
  // each call — set to null on a fully-clean add so a stale message clears.
  addFiles: (incoming) =>
    set((s) => {
      // maxUploadSize (backend MAX_UPLOAD_SIZE via /meta) drives the per-file
      // size gate; null until fetched → partitionStageable skips it. This is the
      // ONE general cap; the backend's tighter text-path limit
      // (MAX_TEXT_CONVERT_BYTES) is intentionally backend-only — see
      // partitionStageable's doc for why we don't mirror it here.
      const maxBytes = useConfigStore.getState().maxUploadSize ?? undefined;
      const { accepted, rejected } = partitionStageable(incoming, maxBytes);
      const room = Math.max(0, MAX_CHAT_ATTACHMENTS - s.files.length);
      const toStage = accepted.slice(0, room);
      const overflow = accepted.length - toStage.length;
      // Dedup names against the existing staged set AND within this batch. On a
      // collision, replace the File with a renamed clone (new File wraps the same
      // bytes by reference — cheap); file.name then carries the unique name, so
      // every consumer (chip display, multipart upload, ImagePreview name-match)
      // stays unchanged. Non-colliding files keep their original File identity.
      const used = new Set(s.files.map((f) => f.file.name));
      const toAdd = toStage.map((file) => {
        const name = uniqueFileName(file.name, used);
        used.add(name);
        const staged =
          name === file.name
            ? file
            : new File([file], name, { type: file.type, lastModified: file.lastModified });
        return { id: nextId(), file: staged };
      });
      const notice: StageNotice | null =
        rejected.length || overflow ? { rejected, overflow } : null;
      return {
        files: toAdd.length ? [...s.files, ...toAdd] : s.files,
        notice,
      };
    }),
  removeFile: (id) => set((s) => ({ files: s.files.filter((f) => f.id !== id) })),
  removeFiles: (ids) => set((s) => ({ files: s.files.filter((f) => !ids.includes(f.id)) })),
  markSent: (ids) =>
    set((s) => ({
      files: s.files.map((f) => (ids.includes(f.id) ? { ...f, sent: true } : f)),
    })),
  clearSent: () => set((s) => ({ files: s.files.filter((f) => !f.sent) })),
  unmarkSent: () =>
    set((s) => ({
      files: s.files.some((f) => f.sent)
        ? s.files.map((f) => (f.sent ? { ...f, sent: false } : f))
        : s.files,
    })),
  dismissNotice: () => set({ notice: null }),
  activate: (key) =>
    set((s) => {
      if (key === s.activeKey) return s;
      // A draft is unsent content only: sent files belong to an in-flight turn
      // whose SSE is torn down on switch (the old clear() dropped them too), so
      // they are not archived. Stash the live slot under the old key when it
      // holds something; otherwise drop any stale archive entry so the map
      // doesn't accumulate blank drafts for every conversation ever visited.
      const draftFiles = s.files.filter((f) => !f.sent);
      const archive = { ...s.archive };
      if (s.text.trim() || draftFiles.length) {
        archive[s.activeKey] = { text: s.text, files: draftFiles };
      } else {
        delete archive[s.activeKey];
      }
      const restored = archive[key] ?? { text: '', files: [] };
      delete archive[key];
      return {
        activeKey: key,
        text: restored.text,
        files: restored.files,
        notice: null,
        archive,
      };
    }),
  promoteNewDraft: (id) =>
    set((s) => (s.activeKey === NEW_DRAFT_KEY ? { activeKey: id } : s)),
}));
