import { create } from 'zustand';
import { MAX_CHAT_ATTACHMENTS } from '@/lib/constants';
import { partitionStageable, type StageRejection } from '@/lib/uploadFilter';

// Files staged in the composer (via the file button, drag-drop, or a huge
// paste) but not yet uploaded. They ride the next message: useChat.sendMessage
// posts them as multipart attachments to POST /chat, which converts each into a
// user_upload artifact before the turn starts. Shared store because drag-drop
// lives in ChatPanel while the button / paste / chips live in MessageInput.

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

interface StagedFilesState {
  files: StagedFile[];
  notice: StageNotice | null;
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
  clear: () => void;
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
  files: [],
  notice: null,
  // Gate then cap, in that order, so every entry point (button / drag-drop /
  // paste-to-stage) behaves identically:
  //   1. drop extensions the backend rejects on sight (avoids a doomed 422 +
  //      the partial-batch orphan it could leave behind);
  //   2. cap the remainder at MAX_CHAT_ATTACHMENTS (the backend also 422s past
  //      this, but staging caps it so that's unreachable in normal use).
  // Anything dropped by either step is reported via `notice` (a drop that
  // bypasses the disabled button still surfaces here). `notice` is replaced
  // each call — set to null on a fully-clean add so a stale message clears.
  addFiles: (incoming) =>
    set((s) => {
      const { accepted, rejected } = partitionStageable(incoming);
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
  clear: () => set({ files: [], notice: null }),
}));
