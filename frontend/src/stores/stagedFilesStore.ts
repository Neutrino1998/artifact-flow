import { create } from 'zustand';

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
  addFiles: (incoming) =>
    set((s) => ({
      files: [...s.files, ...incoming.map((file) => ({ id: nextId(), file }))],
    })),
  removeFile: (id) => set((s) => ({ files: s.files.filter((f) => f.id !== id) })),
  clear: () => set({ files: [] }),
}));
