import { create } from 'zustand';
import { MAX_CHAT_ATTACHMENTS } from '@/lib/constants';

// Files staged in the composer (via the file button, drag-drop, or a huge
// paste) but not yet uploaded. They ride the next message: useChat.sendMessage
// posts them as multipart attachments to POST /chat, which converts each into a
// user_upload artifact before the turn starts. Shared store because drag-drop
// lives in ChatPanel while the button / paste / chips live in MessageInput.

export interface StagedFile {
  id: string;
  file: File;
}

interface StagedFilesState {
  files: StagedFile[];
  addFiles: (files: File[]) => void;
  removeFile: (id: string) => void;
  clear: () => void;
}

let _seq = 0;
function nextId(): string {
  _seq += 1;
  return `staged-${Date.now()}-${_seq}`;
}

export const useStagedFilesStore = create<StagedFilesState>((set) => ({
  files: [],
  // Cap total staged at MAX_CHAT_ATTACHMENTS regardless of entry point
  // (button / drag-drop / paste-to-stage) so the backend 422 is unreachable
  // in normal use. Extras beyond the cap are dropped; the UI disables the
  // attach affordance and shows the count once at the cap.
  addFiles: (incoming) =>
    set((s) => {
      const room = MAX_CHAT_ATTACHMENTS - s.files.length;
      if (room <= 0) return s;
      const toAdd = incoming.slice(0, room).map((file) => ({ id: nextId(), file }));
      return { files: [...s.files, ...toAdd] };
    }),
  removeFile: (id) => set((s) => ({ files: s.files.filter((f) => f.id !== id) })),
  clear: () => set({ files: [] }),
}));
