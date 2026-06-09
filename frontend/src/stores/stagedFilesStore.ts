import { create } from 'zustand';
import { MAX_CHAT_ATTACHMENTS } from '@/lib/constants';
import { partitionStageable, type StageRejection } from '@/lib/uploadFilter';
import { useConfigStore } from '@/stores/configStore';

// The composer draft store. Holds every conversation's UNSENT composer content
// (text + staged files) in one keyed map; the active conversation's draft is
// just `drafts[activeKey]` (absent ⇒ blank). In-memory only — File bytes can't
// round-trip localStorage and the feature is scoped to in-page caching, so a
// reload starts every composer blank, by design. Shared store because drag-drop
// lives in ChatPanel while the button / paste / chips / text live in MessageInput.
//
// Why a store, not MessageInput-local state: switchConversation flips
// currentLoading, which unmounts MessageInput (the loading placeholder), so
// component-local state can't survive a switch. Store state can.
//
// Send model — CLEAR ON SEND, by OWNER key (see useComposerSend):
//   A send belongs to a specific conversation (the activeKey when it started),
//   NOT "whatever's on screen now". The instant the user hits send the composer
//   clears the OWNER draft — drops its text + the files that rode the POST —
//   before the network await. There is deliberately NO restore: a failed send is
//   a best-effort loss (the error is surfaced; the user retypes). This is the
//   explicit scope contract that replaced an earlier "claim + restore-on-failure"
//   reconcile whose every manifestation (restore with no File snapshot after a
//   leave dropped the bytes; a shared new-chat key letting one failed send's
//   content restore into another) was its own reviewer round. Clearing by OWNER
//   key keeps the one property worth keeping: navigating away mid-send can't
//   resurface the outgoing content (it already left the draft) or touch another
//   conversation, and two sends are independent.

// The new chat has no id until its first turn lands one, so its draft lives under
// a single stable sentinel key — NOT a per-click unique key. There's only one
// "new chat" entry in the UI (the new-chat button), so a stable key is what lets
// an unsent new-chat draft survive navigating away and clicking back into the new
// chat. A successful first send promotes this key to the real id (so the next new
// chat starts blank); a failed send cleared the draft like any other (retype) —
// no cross-conversation leak because there's no restore to misfire.
export const NEW_DRAFT_KEY = '__new__';

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

// One conversation's unsent composer draft.
interface Draft {
  text: string;
  files: StagedFile[];
}

interface ComposerState {
  // All conversations' unsent drafts, keyed by conversation id (or a new-chat
  // temp key). The active composer is drafts[activeKey] — absent means blank.
  drafts: Record<string, Draft>;
  activeKey: string;
  // Transient, active-view only: why a picked file didn't stage. Cleared on switch.
  notice: StageNotice | null;

  // --- active-view composer edits (operate on activeKey) ---
  setText: (text: string) => void;
  addFiles: (files: File[]) => void;
  removeFile: (id: string) => void;
  dismissNotice: () => void;

  // --- send, OWNER-keyed (key captured at send start) ---
  // The send is committed → clear the owner draft's text + drop the files that
  // rode the POST, before the await. No restore: a failed send is a best-effort
  // loss (see header). Text is cleared only if it still equals sentText, so
  // typing during the in-flight window isn't clobbered.
  clearDraft: (key: string, sentText: string, sentIds: string[]) => void;

  // --- navigation ---
  activate: (key: string) => void; // switch to an existing conversation
  startNewDraft: () => void; // open the new chat (stable sentinel key)
  promoteNewDraft: (id: string) => void; // a new chat's POST returned its real id
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

// Immutably replace drafts[key] with fn(current), pruning the entry when the
// result is blank (no text, no files) so the map doesn't accumulate empties for
// every conversation ever visited. Keep is exact-empty, not trimmed, so typing
// whitespace doesn't vanish.
function withDraft(
  drafts: Record<string, Draft>,
  key: string,
  fn: (d: Draft) => Draft,
): Record<string, Draft> {
  const next = fn(drafts[key] ?? { text: '', files: [] });
  const out = { ...drafts };
  if (next.text !== '' || next.files.length > 0) out[key] = next;
  else delete out[key];
  return out;
}

export const useStagedFilesStore = create<ComposerState>((set) => ({
  drafts: {},
  activeKey: NEW_DRAFT_KEY,
  notice: null,

  setText: (text) =>
    set((s) => ({ drafts: withDraft(s.drafts, s.activeKey, (d) => ({ ...d, text })) })),

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
      const cur = s.drafts[s.activeKey] ?? { text: '', files: [] };
      // maxUploadSize (backend MAX_UPLOAD_SIZE via /meta) drives the per-file
      // size gate; null until fetched → partitionStageable skips it. This is the
      // ONE general cap; the backend's tighter text-path limit
      // (MAX_TEXT_CONVERT_BYTES) is intentionally backend-only — see
      // partitionStageable's doc for why we don't mirror it here.
      const maxBytes = useConfigStore.getState().maxUploadSize ?? undefined;
      const { accepted, rejected } = partitionStageable(incoming, maxBytes);
      const room = Math.max(0, MAX_CHAT_ATTACHMENTS - cur.files.length);
      const toStage = accepted.slice(0, room);
      const overflow = accepted.length - toStage.length;
      // Dedup names against the existing staged set AND within this batch. On a
      // collision, replace the File with a renamed clone (new File wraps the same
      // bytes by reference — cheap); file.name then carries the unique name, so
      // every consumer (chip display, multipart upload, ImagePreview name-match)
      // stays unchanged. Non-colliding files keep their original File identity.
      const used = new Set(cur.files.map((f) => f.file.name));
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
      const drafts = toAdd.length
        ? withDraft(s.drafts, s.activeKey, (d) => ({ ...d, files: [...d.files, ...toAdd] }))
        : s.drafts;
      return { drafts, notice };
    }),

  removeFile: (id) =>
    set((s) => ({
      drafts: withDraft(s.drafts, s.activeKey, (d) => ({
        ...d,
        files: d.files.filter((f) => f.id !== id),
      })),
    })),

  dismissNotice: () => set({ notice: null }),

  clearDraft: (key, sentText, sentIds) =>
    set((s) => ({
      drafts: withDraft(s.drafts, key, (d) => ({
        // Clear the text we just sent (only if it still equals sentText, so
        // typing during the in-flight window isn't clobbered) and drop the files
        // that rode the POST. No restore counterpart — see header.
        text: d.text === sentText ? '' : d.text,
        files: d.files.filter((f) => !sentIds.includes(f.id)),
      })),
    })),

  // Navigation just swaps the active key + clears the transient notice. Each
  // conversation's unsent draft persists in `drafts` untouched (that's the
  // feature); the new chat's stable key means clicking back into it restores any
  // in-progress draft.
  activate: (key) =>
    set((s) => (key === s.activeKey ? s : { activeKey: key, notice: null })),

  startNewDraft: () => set({ activeKey: NEW_DRAFT_KEY, notice: null }),

  promoteNewDraft: (id) =>
    set((s) => {
      // Only the not-yet-saved new chat carries the sentinel key; an existing
      // conv's activeKey is already its id (no-op). Relabel the draft to the
      // real id so the sentinel is free for the next new chat and a later switch
      // back keys off the conversation.
      if (s.activeKey !== NEW_DRAFT_KEY) return s;
      const drafts = { ...s.drafts };
      if (s.activeKey in drafts) {
        drafts[id] = drafts[s.activeKey];
        delete drafts[s.activeKey];
      }
      return { drafts, activeKey: id };
    }),
}));
