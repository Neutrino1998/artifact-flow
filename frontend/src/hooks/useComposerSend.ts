'use client';

import { useState, useRef, useCallback } from 'react';
import { useStagedFilesStore, type StagedFile } from '@/stores/stagedFilesStore';

// The composer send lifecycle, in one place. A send (new turn) and an inject
// (into a running turn) share one shape across the async gap:
//
//   1. lock      — bail if a send is already in flight (defeats a fast
//                  double-Enter / double-click; a ref, so it's synchronous and
//                  immune to stale closures).
//   2. snapshot  — capture text + staged ids + the OWNER key (the conversation
//                  this send belongs to = activeKey now) BEFORE the await.
//   3. claim     — clear the owner draft's sent text + mark its files in-flight
//                  NOW, before the await. The send is committed the instant it
//                  starts, so its content leaves the draft immediately. This is
//                  the ordinary "clear the box on send" — and it's what makes
//                  navigate-during-send safe: there's nothing left in the draft
//                  for a navigation to resurface, and ops are OWNER-keyed, never
//                  "whatever's on screen now".
//   4. await     — run the network op.
//   5. on result — failure (false / throw): restore the OWNER draft (text only
//                  if the slot is still empty, so typing during the in-flight
//                  window isn't clobbered). Success: nothing — the content is
//                  already gone and the turn's terminal resolves the sent files.
//
// Owner-keying is the whole point: two conversations' sends are independent (no
// shared in-flight flag), and a return that arrives after the user navigated
// away touches only the owner's draft. See stagedFilesStore's send-model note.

type RunFn = (text: string, files: File[] | undefined) => Promise<boolean>;

export interface ComposerOpDeps {
  // The conversation this send belongs to (activeKey at call time).
  ownerKey: string;
  // Snapshot inputs (read once, at call time).
  content: string;
  staged: StagedFile[];
  // Owner-keyed draft mutations (claim before await; restore on failure).
  claimSend: (key: string, sentText: string, sentIds: string[]) => void;
  restoreSend: (key: string, sentText: string, sentIds: string[]) => void;
  // Re-entrancy lock; a ref-like cell so it's shared across renders.
  lockRef: { current: boolean };
  // Optional UI busy flag (only the new-message send shows a spinner).
  setSending?: (busy: boolean) => void;
  // Permit an empty send (no text, no files). Used by a compact-only turn:
  // force_compact rides the request and the backend injects a directive body,
  // so the "nothing to send" bail must not fire.
  allowEmpty?: boolean;
  // The network op. Returns true on success; false or throw → restore.
  run: RunFn;
}

export async function runComposerOp({
  ownerKey,
  content,
  staged,
  claimSend,
  restoreSend,
  lockRef,
  setSending,
  allowEmpty,
  run,
}: ComposerOpDeps): Promise<void> {
  if (lockRef.current) return;
  const trimmed = content.trim();
  const files = staged.map((s) => s.file);
  // Allow files-only (empty text + attachments) and, when allowEmpty (compact-only
  // send), a fully empty send; otherwise bail when there's nothing to send.
  if (!trimmed && files.length === 0 && !allowEmpty) return;

  const sentText = content;
  const sentIds = staged.map((s) => s.id);
  lockRef.current = true;
  setSending?.(true);
  // Claim before the await (see header): the content leaves the owner draft now.
  claimSend(ownerKey, sentText, sentIds);
  try {
    const ok = await run(trimmed, files.length ? files : undefined);
    if (!ok) restoreSend(ownerKey, sentText, sentIds);
  } catch (err) {
    // Network layer (useChat / injectMessage) owns surfacing the error; we just
    // put the composer back so the user can retry.
    console.error('Composer send failed:', err);
    restoreSend(ownerKey, sentText, sentIds);
  } finally {
    lockRef.current = false;
    setSending?.(false);
  }
}

/**
 * Binds {@link runComposerOp} to the composer's store-backed draft.
 *
 * @param ownerKey    the active conversation key (the send's owner)
 * @param content     current draft text (the snapshot source)
 * @param stagedFiles current draft attachments
 */
export function useComposerSend(ownerKey: string, content: string, stagedFiles: StagedFile[]) {
  // `sending` drives the button spinner/disable; `sendingRef` is the actual
  // re-entrancy guard (state updates lag a render; the ref does not).
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  // Inject is lighter — no spinner (the button doubles as Stop once the box is
  // empty) — but still needs its own lock against a rapid double-fire.
  const injectingRef = useRef(false);
  const claimSend = useStagedFilesStore((s) => s.claimSend);
  const restoreSend = useStagedFilesStore((s) => s.restoreSend);

  // New-message send: text and/or staged attachments ride one POST.
  // allowEmpty=true permits a compact-only send (no text, no files).
  const submit = useCallback(
    (run: RunFn, allowEmpty = false) =>
      runComposerOp({
        ownerKey,
        content,
        staged: stagedFiles,
        claimSend,
        restoreSend,
        lockRef: sendingRef,
        setSending,
        allowEmpty,
        run,
      }),
    [ownerKey, content, stagedFiles, claimSend, restoreSend],
  );

  // Inject into a running turn: text only (attachments don't ride an in-flight
  // turn), no spinner. `run` throws on failure → runComposerOp catches → restore.
  const inject = useCallback(
    (run: (text: string) => Promise<unknown>) =>
      runComposerOp({
        ownerKey,
        content,
        staged: [],
        claimSend,
        restoreSend,
        lockRef: injectingRef,
        run: async (text) => {
          await run(text);
          return true;
        },
      }),
    [ownerKey, content, claimSend, restoreSend],
  );

  return { sending, submit, inject };
}
