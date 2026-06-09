'use client';

import { useState, useRef, useCallback } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { useStagedFilesStore, type StagedFile } from '@/stores/stagedFilesStore';

// The composer send lifecycle, in one place. Sending a message (new turn) and
// injecting into a running turn both follow the same shape across an async gap:
//
//   1. re-entrancy lock  — bail if a send is already in flight (defeats a fast
//                          double-Enter / double-click; ref, so it's synchronous
//                          and immune to stale closures).
//   2. snapshot          — capture exactly what we're sending (text + staged ids)
//                          BEFORE the await.
//   3. await             — run the network op.
//   4. reconcile / keep  — on success, clear the box only if it still holds the
//                          snapshot (a slow send + the user typing a new line
//                          must not be wiped) and remove only the files this send
//                          consumed (files staged during the in-flight window
//                          survive). On failure, leave everything intact.
//
// This shape bugged four reviewer rounds in a row when it lived inline and got
// edited piecemeal (clear-before-await → double-submit → blind-clear). Keeping
// it in one tested function is the single enforcement point so a future edit
// can't reintroduce one facet in just one branch.

type SetContent = Dispatch<SetStateAction<string>>;

export interface ComposerOpDeps {
  // Snapshot inputs (read once, at call time).
  content: string;
  staged: StagedFile[];
  // Reconcile outputs.
  setContent: SetContent;
  markSent: (ids: string[]) => void;
  // Re-entrancy lock; a ref-like cell so it's shared across renders.
  lockRef: { current: boolean };
  // Optional UI busy flag (only the new-message send shows a spinner).
  setSending?: (busy: boolean) => void;
  // Permit an empty send (no text, no files). Used by a compact-only turn:
  // force_compact rides the request and the backend injects a directive body,
  // so the "nothing to send" bail must not fire.
  allowEmpty?: boolean;
  // Optional bracket around the in-flight window (after the lock is taken,
  // before the await; released in finally). The composer uses it to flag the
  // draft store that the active conversation has a send in flight, so a
  // navigation during the await drops the outgoing content instead of
  // archiving it as a draft. Only fires when a send actually happens (past the
  // empty-bail), so it stays paired.
  onSendStart?: () => void;
  onSendEnd?: () => void;
  // The network op. Returns true on success → reconcile; false or throw → keep.
  run: (text: string, files: File[] | undefined) => Promise<boolean>;
}

export async function runComposerOp({
  content,
  staged,
  setContent,
  markSent,
  lockRef,
  setSending,
  allowEmpty,
  onSendStart,
  onSendEnd,
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
  onSendStart?.();
  try {
    const ok = await run(trimmed, files.length ? files : undefined);
    if (ok) {
      // Reconcile against live state, don't blind-clear.
      setContent((prev) => (prev === sentText ? '' : prev));
      if (sentIds.length) markSent(sentIds);
    }
  } catch (err) {
    // Keep the composer intact so the user can retry; the network layer
    // (useChat / injectMessage) owns surfacing the error to the user.
    console.error('Composer send failed:', err);
  } finally {
    lockRef.current = false;
    setSending?.(false);
    onSendEnd?.();
  }
}

/**
 * Binds {@link runComposerOp} to the composer's React state.
 *
 * @param content      current textarea value (the snapshot source)
 * @param setContent   textarea setter (functional form is used for reconcile)
 * @param stagedFiles  staged attachments
 * @param markSent  store action marking the ids a send consumed as in-flight
 *                  (kept visible until the turn's terminal event resolves them)
 */
export function useComposerSend(
  content: string,
  setContent: SetContent,
  stagedFiles: StagedFile[],
  markSent: (ids: string[]) => void,
) {
  // `sending` drives the button spinner/disable; `sendingRef` is the actual
  // re-entrancy guard (state updates lag a render; the ref does not).
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  // Inject is lighter — no spinner (the button doubles as Stop once the box is
  // empty) — but still needs its own lock against a rapid double-fire.
  const injectingRef = useRef(false);
  // Flag the draft store across the in-flight window (stable zustand actions),
  // so navigating away mid-send drops the outgoing content instead of archiving
  // it as a draft (see stagedFilesStore.activate). Covers both send and inject.
  const markSendStart = useStagedFilesStore((s) => s.markSendStart);
  const markSendEnd = useStagedFilesStore((s) => s.markSendEnd);

  // New-message send: text and/or staged attachments ride one POST.
  // allowEmpty=true permits a compact-only send (no text, no files).
  const submit = useCallback(
    (run: (text: string, files: File[] | undefined) => Promise<boolean>, allowEmpty = false) =>
      runComposerOp({
        content,
        staged: stagedFiles,
        setContent,
        markSent,
        lockRef: sendingRef,
        setSending,
        allowEmpty,
        onSendStart: markSendStart,
        onSendEnd: markSendEnd,
        run,
      }),
    [content, stagedFiles, setContent, markSent, markSendStart, markSendEnd],
  );

  // Inject into a running turn: text only (attachments don't ride an in-flight
  // turn), no spinner. `run` throws on failure → runComposerOp catches → keep.
  const inject = useCallback(
    (run: (text: string) => Promise<unknown>) =>
      runComposerOp({
        content,
        staged: [],
        setContent,
        markSent,
        lockRef: injectingRef,
        onSendStart: markSendStart,
        onSendEnd: markSendEnd,
        run: async (text) => {
          await run(text);
          return true;
        },
      }),
    [content, setContent, markSent, markSendStart, markSendEnd],
  );

  return { sending, submit, inject };
}
