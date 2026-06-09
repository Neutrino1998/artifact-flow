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
// Send model — CLAIM AT SEND START, reconcile by OWNER key (see useComposerSend):
//   A send belongs to a specific conversation (the activeKey when it started),
//   NOT "whatever's on screen now". So the composer claims the OWNER draft —
//   clears its sent text + marks its files in-flight — the instant the user hits
//   send, BEFORE the network await; on failure it restores the OWNER draft.
//   Consequences that kill a whole class of bug at once: navigating away
//   mid-send can't resurface the outgoing content (it already left the draft) or
//   clobber another conversation (every send op is owner-keyed, never the "live
//   slot"), and two conversations' sends are independent. This replaced an
//   earlier "single live slot + post-await reconcile + in-flight flag" model
//   whose every manifestation (resurfaced content → duplicate upload,
//   cross-conversation clobber, concurrent-send flag overwrite) was its own bug.

// The new chat has no id until its first turn lands one, so its draft lives
// under a single stable sentinel key — NOT a per-click unique key. There's only
// one "new chat" entry in the UI (the new-chat button), so a stable key is what
// lets an unsent new-chat draft survive navigating away and clicking back into
// the new chat. A successful first send promotes this key to the real id (so the
// next new chat starts blank); a failed send simply restores the content here,
// which is the same new chat the user sent from — retry, not a cross-conversation
// leak. (An earlier per-click unique key fixed that failure path by stashing the
// content under a key the user could never navigate back to — i.e. it just hid
// it — at the cost of losing the draft on every new-chat click. Not worth it.)
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

  // --- send lifecycle, OWNER-keyed (key captured at send start) ---
  // claimSend: the send is committed → clear its text + mark its files in-flight,
  // before the await. restoreSend: the send failed → revert (text only if the
  // slot is still empty, so typing during the in-flight window isn't clobbered).
  claimSend: (key: string, sentText: string, sentIds: string[]) => void;
  restoreSend: (key: string, sentText: string, sentIds: string[]) => void;

  // --- turn terminal, conversation-keyed (see useSSE.resolveStagedAfterTerminal) ---
  // flushed → drop the sent files; not flushed → revert to staged for retry.
  clearSent: (key: string) => void;
  unmarkSent: (key: string) => void;

  // --- navigation ---
  activate: (key: string) => void; // switch to an existing conversation
  startNewDraft: () => void; // open a fresh new chat (unique temp key)
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

// Dropping the sent (committed/in-flight) files from a draft on leave: they
// belong to the turn, not the draft, and are resolved via the terminal only
// while the conversation is active (its SSE is torn down on switch) — keeping
// them would leave stale "sent" chips. Unsent text/files remain the draft.
function dropSentOnLeave(drafts: Record<string, Draft>, key: string): Record<string, Draft> {
  return withDraft(drafts, key, (d) => ({ ...d, files: d.files.filter((f) => !f.sent) }));
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

  claimSend: (key, sentText, sentIds) =>
    set((s) => ({
      drafts: withDraft(s.drafts, key, (d) => ({
        // The text we just sent equals sentText at send time → clear it; mark
        // the sent files in-flight (kept visible until the turn's terminal).
        text: d.text === sentText ? '' : d.text,
        files: d.files.map((f) => (sentIds.includes(f.id) ? { ...f, sent: true } : f)),
      })),
    })),

  restoreSend: (key, sentText, sentIds) =>
    set((s) => ({
      drafts: withDraft(s.drafts, key, (d) => ({
        // Restore the sent text only if the slot is still empty — anything typed
        // during the in-flight window wins. Revert the sent files to staged.
        text: d.text === '' ? sentText : d.text,
        files: d.files.map((f) => (sentIds.includes(f.id) ? { ...f, sent: false } : f)),
      })),
    })),

  clearSent: (key) =>
    set((s) => {
      const d = s.drafts[key];
      if (!d || !d.files.some((f) => f.sent)) return s;
      return {
        drafts: withDraft(s.drafts, key, (dd) => ({
          ...dd,
          files: dd.files.filter((f) => !f.sent),
        })),
      };
    }),

  unmarkSent: (key) =>
    set((s) => {
      const d = s.drafts[key];
      if (!d || !d.files.some((f) => f.sent)) return s;
      return {
        drafts: withDraft(s.drafts, key, (dd) => ({
          ...dd,
          files: dd.files.map((f) => (f.sent ? { ...f, sent: false } : f)),
        })),
      };
    }),

  activate: (key) =>
    set((s) => {
      if (key === s.activeKey) return s;
      return { activeKey: key, notice: null, drafts: dropSentOnLeave(s.drafts, s.activeKey) };
    }),

  startNewDraft: () =>
    set((s) => ({
      // Open the new chat (stable key). Drop the leaving conversation's sent
      // files (incl. the new chat's own, if a send is in flight — abandoning it
      // for a fresh start), but KEEP any unsent new-chat draft: clicking the
      // new-chat button is also how the user returns to an in-progress new chat.
      activeKey: NEW_DRAFT_KEY,
      notice: null,
      drafts: dropSentOnLeave(s.drafts, s.activeKey),
    })),

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
