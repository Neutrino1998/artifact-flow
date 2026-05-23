'use client';

import { useState, useRef, useCallback } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import type { StagedFile } from '@/stores/stagedFilesStore';

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
  removeFiles: (ids: string[]) => void;
  // Re-entrancy lock; a ref-like cell so it's shared across renders.
  lockRef: { current: boolean };
  // Optional UI busy flag (only the new-message send shows a spinner).
  setSending?: (busy: boolean) => void;
  // The network op. Returns true on success → reconcile; false or throw → keep.
  run: (text: string, files: File[] | undefined) => Promise<boolean>;
}

export async function runComposerOp({
  content,
  staged,
  setContent,
  removeFiles,
  lockRef,
  setSending,
  run,
}: ComposerOpDeps): Promise<void> {
  if (lockRef.current) return;
  const trimmed = content.trim();
  const files = staged.map((s) => s.file);
  // Allow files-only (empty text + attachments); bail only if there's nothing.
  if (!trimmed && files.length === 0) return;

  const sentText = content;
  const sentIds = staged.map((s) => s.id);
  lockRef.current = true;
  setSending?.(true);
  try {
    const ok = await run(trimmed, files.length ? files : undefined);
    if (ok) {
      // Reconcile against live state, don't blind-clear.
      setContent((prev) => (prev === sentText ? '' : prev));
      if (sentIds.length) removeFiles(sentIds);
    }
  } catch (err) {
    // Keep the composer intact so the user can retry; the network layer
    // (useChat / injectMessage) owns surfacing the error to the user.
    console.error('Composer send failed:', err);
  } finally {
    lockRef.current = false;
    setSending?.(false);
  }
}

/**
 * Binds {@link runComposerOp} to the composer's React state.
 *
 * @param content      current textarea value (the snapshot source)
 * @param setContent   textarea setter (functional form is used for reconcile)
 * @param stagedFiles  staged attachments
 * @param removeFiles  store action to drop the ids a send consumed
 */
export function useComposerSend(
  content: string,
  setContent: SetContent,
  stagedFiles: StagedFile[],
  removeFiles: (ids: string[]) => void,
) {
  // `sending` drives the button spinner/disable; `sendingRef` is the actual
  // re-entrancy guard (state updates lag a render; the ref does not).
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  // Inject is lighter — no spinner (the button doubles as Stop once the box is
  // empty) — but still needs its own lock against a rapid double-fire.
  const injectingRef = useRef(false);

  // New-message send: text and/or staged attachments ride one POST.
  const submit = useCallback(
    (run: (text: string, files: File[] | undefined) => Promise<boolean>) =>
      runComposerOp({
        content,
        staged: stagedFiles,
        setContent,
        removeFiles,
        lockRef: sendingRef,
        setSending,
        run,
      }),
    [content, stagedFiles, setContent, removeFiles],
  );

  // Inject into a running turn: text only (attachments don't ride an in-flight
  // turn), no spinner. `run` throws on failure → runComposerOp catches → keep.
  const inject = useCallback(
    (run: (text: string) => Promise<unknown>) =>
      runComposerOp({
        content,
        staged: [],
        setContent,
        removeFiles,
        lockRef: injectingRef,
        run: async (text) => {
          await run(text);
          return true;
        },
      }),
    [content, setContent, removeFiles],
  );

  return { sending, submit, inject };
}
