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
  dismissNotice: () => void;
  clear: () => void;
}

let _seq = 0;
function nextId(): string {
  _seq += 1;
  return `staged-${Date.now()}-${_seq}`;
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
      const toAdd = toStage.map((file) => ({ id: nextId(), file }));
      const notice: StageNotice | null =
        rejected.length || overflow ? { rejected, overflow } : null;
      return {
        files: toAdd.length ? [...s.files, ...toAdd] : s.files,
        notice,
      };
    }),
  removeFile: (id) => set((s) => ({ files: s.files.filter((f) => f.id !== id) })),
  removeFiles: (ids) => set((s) => ({ files: s.files.filter((f) => !ids.includes(f.id)) })),
  dismissNotice: () => set({ notice: null }),
  clear: () => set({ files: [], notice: null }),
}));
