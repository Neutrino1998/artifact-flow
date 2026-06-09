'use client';

import { useState, useRef, useCallback, useEffect } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { useChat } from '@/hooks/useChat';
import { useComposerSend } from '@/hooks/useComposerSend';
import { useStreamStore } from '@/stores/streamStore';
import { useUIStore } from '@/stores/uiStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useConfigStore } from '@/stores/configStore';
import { useStagedFilesStore } from '@/stores/stagedFilesStore';
import StagedFileChip from './StagedFileChip';
import { injectMessage, cancelExecution } from '@/lib/api';
import type { UploadEvent } from '@/lib/api';
import { formatTokens } from '@/lib/formatTokens';
import { formatBytes } from '@/lib/formatBytes';
import { MAX_MESSAGE_CHARS, MAX_CHAT_ATTACHMENTS } from '@/lib/constants';

// Composer upload-progress state. `uploading` carries live byte counts from
// xhr.upload.onprogress; `processing` is the gap between "last byte sent"
// (xhr.upload.onload) and the server's ChatResponse — the bar sits at 100%
// in that window and switches its label so the user knows we're not stuck.
// Only allocated when the send carried files; cleared in finally so a
// success/failure/throw all converge to the same idle state.
type UploadProgress =
  | { phase: 'uploading'; loaded: number; total: number; lengthComputable: boolean }
  | { phase: 'processing' };

// The synthetic name browsers attach to a clipboard image that has no backing
// file (a screenshot or a "copy image" — Chrome/Edge/Firefox all use
// "image.<ext>"). We rename only these placeholders, NOT every image/* paste:
// an actual image file copied from the OS file manager carries its real name
// (e.g. "vacation.jpg") and must be left untouched.
const GENERIC_CLIPBOARD_IMAGE = /^image\.(png|jpe?g|gif|webp|bmp)$/i;

export default function MessageInput() {
  // Composer text lives in the staged-files store, not local state: switching
  // conversations flips currentLoading, which unmounts this component (the
  // loading placeholder), so local state can't survive a switch. The store
  // keeps it as a per-conversation draft (see stagedFilesStore). setContent
  // adapts the store setter to the SetStateAction signature useComposerSend's
  // reconcile expects (it calls the functional updater form against live text).
  const content = useStagedFilesStore((s) => s.text);
  const setText = useStagedFilesStore((s) => s.setText);
  const setContent = useCallback<Dispatch<SetStateAction<string>>>(
    (v) =>
      setText(
        typeof v === 'function'
          ? (v as (prev: string) => string)(useStagedFilesStore.getState().text)
          : v,
      ),
    [setText],
  );
  // Armed by the "compact" toggle; rides the next send as force_compact and is
  // cleared on a successful send. A compact-only send (no text) is allowed.
  const [forceCompact, setForceCompact] = useState(false);
  // null when idle; only set when a send carried files (text-only sends finish
  // too fast for a progress bar to be useful). Lifecycle is owned by handleSend
  // — it sets this in the onUpload callback and clears it in the finally branch.
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isComposingRef = useRef(false);
  const { sendMessage, isNewConversation } = useChat();
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const cancelling = useStreamStore((s) => s.cancelling);
  const setCancelling = useStreamStore((s) => s.setCancelling);
  // QUEUED marker: set on the execution_queued SSE event, cleared on the first
  // agent_start (turn started RUNNING) / endStream / reset. While set, the turn
  // is parked in a worker-local concurrency semaphore and is neither cancellable
  // nor injectable — both endpoints gate on the engine being interactive (RUNNING)
  // and 409 otherwise. We use it to disable the composer action button so it
  // doesn't silently no-op during the wait.
  const queuedInfo = useStreamStore((s) => s.queuedInfo);
  const toggleArtifactPanel = useUIStore((s) => s.toggleArtifactPanel);

  const stagedFiles = useStagedFilesStore((s) => s.files);
  const addFiles = useStagedFilesStore((s) => s.addFiles);
  const removeFile = useStagedFilesStore((s) => s.removeFile);
  const markSent = useStagedFilesStore((s) => s.markSent);
  const stageNotice = useStagedFilesStore((s) => s.notice);
  const dismissNotice = useStagedFilesStore((s) => s.dismissNotice);

  // Snapshot → lock → await → reconcile/keep for both send and inject lives in
  // this hook (single enforcement point); see useComposerSend.ts. On send-ok the
  // consumed files are marked sent (kept visible until the turn's terminal event:
  // COMPLETE clears them, cancel/error/timeout reverts — uploads are ephemeral).
  const { sending, submit, inject } = useComposerSend(content, setContent, stagedFiles, markSent);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [content]);

  const conversationId = useConversationStore((s) => s.current?.id);
  const streamConversationId = useStreamStore((s) => s.conversationId);

  // Context-usage gauge: how much context the next message will carry, vs the
  // backend auto-compaction threshold. Sourced from the persisted branch tail's
  // `execution_metrics` — the last lead LLM call's `last_input_tokens +
  // last_output_tokens`, matching the compaction trigger (input+output >
  // threshold) so the gauge and the model-facing <context_usage> warning read
  // the same number. If the turn ended on a compaction (triggered on the final
  // response, no further lead call), the backend overrides last_input_tokens
  // with the summary size and zeroes last_output_tokens, so the gauge correctly
  // drops post-compaction. lead-only by convention; subagent compaction does
  // not pollute these fields. See docs/architecture/engine.md.
  // Non-live: updates after each completed turn / on conversation load.
  const branchPath = useConversationStore((s) => s.branchPath);
  const compactionThreshold = useConfigStore((s) => s.compactionThreshold);
  const leadAgentModel = useConfigStore((s) => s.leadAgentModel);
  const fetchConfig = useConfigStore((s) => s.fetchConfig);
  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);
  const lastNode = branchPath.length > 0 ? branchPath[branchPath.length - 1] : null;
  const lastMetrics = lastNode?.execution_metrics as
    | { last_input_tokens?: number | null; last_output_tokens?: number | null }
    | null
    | undefined;
  const contextTokens =
    lastMetrics?.last_input_tokens != null
      ? lastMetrics.last_input_tokens + (lastMetrics.last_output_tokens ?? 0)
      : null;

  // Compact is meaningless with no history to summarize — and worse, the
  // injected directive ("history will be compacted right after your response")
  // tends to hallucinate on a blank first turn (model invents prior context to
  // describe).
  //
  // branchPath is derived from persisted `current`, so length>0 ⇔ "at least
  // one turn already landed in DB" — covers both the blank-new-chat state and
  // the first-turn-in-flight state (where `current` is still null pre-refresh).
  //
  // Correctness goes through the derived `effectiveForceCompact`, never raw
  // `forceCompact` — because the useEffect cleanup below is async (one render
  // late), keyboard Enter would otherwise punch through the button-disabled
  // guard in the one-frame window where `forceCompact=true && !hasPersisted`.
  // The effect stays as UX cleanup (chip animates away on conv switch) but
  // can't be relied on for behavior.
  const hasPersistedHistory = branchPath.length > 0;
  const effectiveForceCompact = forceCompact && hasPersistedHistory;
  useEffect(() => {
    if (!hasPersistedHistory && forceCompact) setForceCompact(false);
  }, [hasPersistedHistory, forceCompact]);

  const handleSend = useCallback(async () => {
    if (isStreaming && !content.trim()) {
      // Stop: cancel backend execution. The cancel signal queues into the
      // engine — it only takes effect at the next checkpoint — so flip to a
      // "cancelling…" state immediately for feedback. endStream() (fired by
      // any terminal SSE event) clears it.
      if (cancelling) return;
      const convId = streamConversationId || conversationId;
      if (convId) {
        try {
          await cancelExecution(convId);
          setCancelling(true);
        } catch (err) {
          console.error('Cancel failed:', err);
        }
      }
      return;
    }

    if (isStreaming) {
      // Inject mode: text only (attachments ride a new message, not an
      // in-flight turn). The hook owns the empty-guard / lock / reconcile.
      const convId = streamConversationId || conversationId;
      if (!convId) return;
      await inject((text) => injectMessage(convId, text));
      return;
    }

    // New-message send: text and/or staged attachments ride one POST. When the
    // compact toggle is armed AND there's history to compact, force_compact
    // rides along (and allowEmpty lets a compact-only send through). Clear the
    // raw `forceCompact` on any successful armed send, even if it didn't take
    // effect this turn (state hygiene — don't leave stale armed state behind).
    const compact = effectiveForceCompact;
    await submit(async (text, files) => {
      // Only show progress for sends that actually carry files — a text-only
      // POST's body is small enough that the bar would flash and vanish.
      const onUpload = files && files.length > 0
        ? (ev: UploadEvent) => {
            if (ev.type === 'progress') {
              setUploadProgress({
                phase: 'uploading',
                loaded: ev.loaded,
                total: ev.total,
                lengthComputable: ev.lengthComputable,
              });
            } else {
              setUploadProgress({ phase: 'processing' });
            }
          }
        : undefined;
      try {
        const ok = await sendMessage(text, undefined, files, compact, onUpload);
        if (ok && forceCompact) setForceCompact(false);
        return ok;
      } finally {
        // Single convergence point: success / failure / throw all clear the
        // bar. The error itself is already surfaced via streamStore.setError
        // by useChat.sendMessage; composer state (text + chips) is preserved
        // by useComposerSend's reconcile-on-success rule.
        setUploadProgress(null);
      }
    }, compact);
  }, [content, isStreaming, cancelling, setCancelling, conversationId, streamConversationId, inject, submit, sendMessage, forceCompact, effectiveForceCompact]);

  const handleCompositionStart = useCallback(() => {
    isComposingRef.current = true;
  }, []);

  const handleCompositionEnd = useCallback(() => {
    // Chrome fires compositionend BEFORE keydown, so delay the reset
    // to ensure the Enter keydown that confirms composition is still blocked
    requestAnimationFrame(() => {
      isComposingRef.current = false;
    });
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      // Match the disabled attach button: attachments ride a new turn, not an
      // in-flight one, so a paste while streaming falls through to plain text.
      if (isStreaming) {
        return;
      }
      const clip = e.clipboardData;
      if (!clip) return;

      // Real files on the clipboard → stage them as attachments. Prefer
      // `clipboardData.files` (modern browsers populate it for both pasted
      // images/screenshots and files copied from the OS file manager); fall
      // back to scanning `items` for kind==='file' for sources that only
      // expose the file there. Reliable case is images/screenshots — copying a
      // file in the OS file manager only reaches the browser on some
      // OS/browser combos (notably not macOS Finder), a platform limit we
      // can't work around. addFiles owns the gate/cap/dedup, so an unsupported
      // or oversize paste surfaces via `notice` like any other add.
      let pasted: File[] = Array.from(clip.files ?? []);
      if (pasted.length === 0 && clip.items) {
        pasted = Array.from(clip.items)
          .filter((it) => it.kind === 'file')
          .map((it) => it.getAsFile())
          .filter((f): f is File => f != null);
      }
      if (pasted.length > 0) {
        e.preventDefault();
        const ts = new Date().toISOString().replace(/[:.]/g, '-');
        // Give a stable, timestamped name to clipboard files that arrive
        // unnamed OR as a browser-synthetic image placeholder ("image.png" —
        // see GENERIC_CLIPBOARD_IMAGE). Without this, repeated screenshot
        // pastes all read "image.png" / "image_1.png" (the store dedups
        // collisions but the names stay generic) and the upload artifact is
        // likewise generic. Each paste event has its own `ts`, so successive
        // pastes get distinct names. Files with a real name (incl. OS-file
        // copies) pass through unchanged.
        const named = pasted.map((f) => {
          const generic =
            !f.name || (f.type.startsWith('image/') && GENERIC_CLIPBOARD_IMAGE.test(f.name));
          if (!generic) return f;
          const ext = f.type.split('/')[1] || 'bin';
          return new File([f], `pasted-${ts}.${ext}`, { type: f.type });
        });
        addFiles(named);
        return;
      }

      // A text paste larger than the message cap is diverted to a staged .txt
      // attachment instead of being inlined (which would hit the 422 cap and
      // bloat context). Divert only if there's room; at the attachment cap,
      // let it paste inline (textarea maxLength caps it) rather than silently
      // dropping it. Smaller pastes fall through to normal insertion.
      const text = clip.getData('text/plain') ?? '';
      if (text.length > MAX_MESSAGE_CHARS && stagedFiles.length < MAX_CHAT_ATTACHMENTS) {
        e.preventDefault();
        const ts = new Date().toISOString().replace(/[:.]/g, '-');
        const file = new File([text], `pasted-${ts}.txt`, { type: 'text/plain' });
        addFiles([file]);
      }
    },
    [isStreaming, addFiles, stagedFiles.length]
  );

  const handleFileSelect = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (files && files.length > 0) {
        addFiles(Array.from(files));
      }
      // Reset input so the same files can be selected again
      e.target.value = '';
    },
    [addFiles]
  );

  const atAttachmentCap = stagedFiles.length >= MAX_CHAT_ATTACHMENTS;
  const attachDisabled = isStreaming || atAttachmentCap;
  const nearLimit = content.length > MAX_MESSAGE_CHARS * 0.8;
  const hasStaged = stagedFiles.length > 0;

  return (
    <div className="relative px-4 pt-4 pb-5">
      {/* Gradient fade above input */}
      <div className="absolute inset-x-0 -top-6 h-6 bg-gradient-to-t from-chat dark:from-chat-dark to-transparent pointer-events-none" />
      <div className="max-w-3xl mx-auto">
        {/* @container marks the composer as a container-query root: the gauge
            and model badge below measure THIS composer's width, not the viewport.
            Viewport breakpoints (`sm:`) were wrong for this layout — chat column
            width is a function of (viewport − sidebar − artifact-panel − whatever
            other side panel happens to be open), and combining all of those
            into a single dynamic breakpoint was brittle. @container makes the
            composer self-aware: it only shows what fits in its own box. */}
        <div
          className="@container bg-surface dark:bg-surface-dark border border-border dark:border-border-dark focus-within:border-accent dark:focus-within:border-accent rounded-2xl shadow-float px-4 py-3 transition-colors"
        >
          {/* Why some picked files weren't staged (unsupported format / over
              the attachment cap). Covers drag-drop too, which bypasses the
              disabled attach button. */}
          {stageNotice && (
            <div className="flex items-start gap-2 mb-2 px-2.5 py-2 rounded-lg border border-accent/40 bg-accent/5 dark:bg-accent/10 text-xs text-text-secondary dark:text-text-secondary-dark">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
                <path d="M12 9v4M12 17h.01" />
              </svg>
              <div className="flex-1 min-w-0 space-y-0.5">
                {stageNotice.rejected.map((r, i) => (
                  <div key={`${r.name}-${i}`} className="break-words">
                    <span className="font-medium">{r.name}</span>：{r.reason}
                  </div>
                ))}
                {stageNotice.overflow > 0 && (
                  <div className="break-words">
                    已达附件上限（最多 {MAX_CHAT_ATTACHMENTS} 个），另外 {stageNotice.overflow} 个文件未添加。
                  </div>
                )}
              </div>
              <button
                onClick={dismissNotice}
                className="shrink-0 p-0.5 rounded hover:bg-accent/10 dark:hover:bg-accent/20"
                aria-label="关闭提示"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}

          {/* Staged attachment chips */}
          {hasStaged && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {stagedFiles.map((sf) => (
                <StagedFileChip key={sf.id} sf={sf} onRemove={() => removeFile(sf.id)} />
              ))}
              <span className="inline-flex items-center px-1 text-xs tabular-nums text-text-tertiary dark:text-text-tertiary-dark">
                {stagedFiles.length}/{MAX_CHAT_ATTACHMENTS}
              </span>
            </div>
          )}

          {/* Compact-armed chip — visible cue that the next send will compact.
              Gated on effectiveForceCompact (not raw forceCompact) so the chip
              never lies about what the send path will actually do. */}
          {effectiveForceCompact && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              <span className="inline-flex items-center gap-1 pl-2 pr-1 py-1 rounded-lg bg-accent/10 border border-accent/40 text-xs text-accent">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                  <polyline points="4 14 10 14 10 20" />
                  <polyline points="20 10 14 10 14 4" />
                </svg>
                <span>本轮回答后压缩上下文</span>
                <button
                  onClick={() => setForceCompact(false)}
                  className="shrink-0 p-0.5 rounded hover:bg-accent/20"
                  aria-label="取消压缩"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                    <path d="M18 6L6 18M6 6l12 12" />
                  </svg>
                </button>
              </span>
            </div>
          )}

          {/* Upload progress — visible only while the attached-files POST is
              in flight. Aggregate (single multipart body, all files together);
              per-file granularity would require N parallel POSTs and break the
              "attachments ride one turn" invariant. Two phases:
                • uploading: bar tracks loaded/total from xhr.upload.onprogress
                • processing: bar pinned at 100% with a different label while
                              the server reads the body + creates user_upload
                              artifacts + enqueues the turn (the gap between
                              last byte sent and ChatResponse received). */}
          {uploadProgress && (
            <div
              className="mb-2 flex items-center gap-2 text-xs text-text-tertiary dark:text-text-tertiary-dark"
              role="status"
              aria-live="polite"
            >
              <div className="flex-1 h-1 rounded-full bg-bg dark:bg-bg-dark overflow-hidden">
                <div
                  className="h-full bg-accent transition-[width] duration-200 ease-out"
                  style={{
                    width:
                      uploadProgress.phase === 'processing'
                        ? '100%'
                        : uploadProgress.lengthComputable && uploadProgress.total > 0
                          ? `${Math.min(100, Math.round((uploadProgress.loaded / uploadProgress.total) * 100))}%`
                          : '15%', // indeterminate fallback — show *something*
                  }}
                />
              </div>
              <span className="font-mono tabular-nums shrink-0 whitespace-nowrap">
                {uploadProgress.phase === 'processing'
                  ? '服务器处理中…'
                  : uploadProgress.lengthComputable && uploadProgress.total > 0
                    ? `上传中 ${Math.min(100, Math.round((uploadProgress.loaded / uploadProgress.total) * 100))}% · ${formatBytes(uploadProgress.loaded)} / ${formatBytes(uploadProgress.total)}`
                    : `上传中 · ${formatBytes(uploadProgress.loaded)}`}
              </span>
            </div>
          )}

          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onCompositionStart={handleCompositionStart}
            onCompositionEnd={handleCompositionEnd}
            maxLength={MAX_MESSAGE_CHARS}
            placeholder={
              isStreaming
                ? '输入追加指令，按 Enter 发送...'
                : isNewConversation
                  ? '开始新的对话...'
                  : '输入消息...'
            }
            rows={1}
            className="w-full resize-none bg-transparent leading-5 text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark outline-none"
          />

          <div className="flex items-center justify-between mt-2">
            <div className="flex items-center gap-1">
              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                onChange={handleFileChange}
                className="hidden"
              />

              {/* Attach file (stages — sent with the next message).
                  h-8 w-8 (not p-1.5) so the hover/focus box matches the Send
                  button's 32×32 outer size — eyes read all four interactive
                  targets in this row as one aligned strip. */}
              <button
                onClick={handleFileSelect}
                disabled={attachDisabled}
                className="h-8 w-8 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                aria-label="Attach file"
                title={atAttachmentCap ? `最多 ${MAX_CHAT_ATTACHMENTS} 个附件` : '添加附件（随消息发送，支持多选）'}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
              </button>

              {/* Artifact panel toggle */}
              <button
                onClick={toggleArtifactPanel}
                className="h-8 w-8 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
                aria-label="Toggle artifact panel"
                title="切换文稿面板"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="1.5" y="2" width="13" height="12" rx="1.5" />
                  <path d="M9.5 2v12" />
                </svg>
              </button>

              {/* Compact context — arms a one-shot compaction on the next send.
                  Disabled while streaming (compaction rides a fresh turn, and the
                  composer can't start one mid-stream). */}
              <button
                onClick={() => setForceCompact((v) => !v)}
                disabled={isStreaming || !hasPersistedHistory}
                className={`h-8 w-8 flex items-center justify-center rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                  effectiveForceCompact
                    ? 'bg-accent/15 text-accent'
                    : 'text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark'
                }`}
                aria-label="Compact context"
                aria-pressed={effectiveForceCompact}
                title={
                  !hasPersistedHistory
                    ? '当前会话无历史可压缩'
                    : effectiveForceCompact
                      ? '已开启压缩：本轮回答后把之前的对话压缩成摘要（点击取消）'
                      : '压缩上下文：本轮回答后把之前的对话压缩成摘要'
                }
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="4 14 10 14 10 20" />
                  <polyline points="20 10 14 10 14 4" />
                  <line x1="14" y1="10" x2="21" y2="3" />
                  <line x1="3" y1="21" x2="10" y2="14" />
                </svg>
              </button>

              {/* Char counter — only when approaching the cap */}
              {nearLimit && (
                <span className="ml-1 text-xs tabular-nums text-text-tertiary dark:text-text-tertiary-dark">
                  {content.length}/{MAX_MESSAGE_CHARS}
                </span>
              )}
            </div>

            {/* Right group: context-usage gauge + unified Send/Stop/Inject button.
                gap-3 (not gap-2) gives the gauge breathing room from the Enter
                button so the eye reads it as info, not a button label. */}
            <div className="flex items-center gap-3">
            {compactionThreshold != null && contextTokens != null && contextTokens > 0 && (() => {
              const pct = Math.min(100, Math.round((contextTokens / compactionThreshold) * 100));
              const near = pct >= 85;
              return (
                <div
                  className="hidden @sm:flex h-8 items-center gap-1.5 text-xs text-text-tertiary dark:text-text-tertiary-dark select-none"
                  title={`下一轮将带入的上下文约 ${contextTokens.toLocaleString()} tokens / 自动压缩阈值 ${compactionThreshold.toLocaleString()}（达到阈值会自动压缩历史；若该轮以压缩结束，此值为压缩摘要大小的实测代理）`}
                >
                  {/* Ring geometry: 16x16 to match the attach/artifact/compact icon
                      glyphs on the left. r=6.5, sw=1.75 keeps stroke inside the viewBox
                      (6.5 + 1.75/2 = 7.375 < 8). -rotate-90 starts the arc at 12 o'clock;
                      dashoffset = circumference * (1 - pct/100) draws it. */}
                  <svg width="16" height="16" viewBox="0 0 16 16" className="-rotate-90 shrink-0">
                    <circle
                      cx="8"
                      cy="8"
                      r={6.5}
                      fill="none"
                      strokeWidth="1.75"
                      stroke="currentColor"
                      className="text-border dark:text-border-dark"
                    />
                    <circle
                      cx="8"
                      cy="8"
                      r={6.5}
                      fill="none"
                      strokeWidth="1.75"
                      strokeLinecap="round"
                      stroke="currentColor"
                      strokeDasharray={2 * Math.PI * 6.5}
                      strokeDashoffset={2 * Math.PI * 6.5 * (1 - pct / 100)}
                      className={near ? 'text-amber-500' : 'text-accent'}
                    />
                  </svg>
                  {/* translate-y-[0.5px]: mono digits 的 cap-center 比 line-box
                      center 略高，flex items-center 居中的是 line-box，所以肉眼
                      看着偏上。亚像素下移补回视觉重心。 */}
                  <span className="font-mono tabular-nums translate-y-[0.5px]">{formatTokens(contextTokens)}/{formatTokens(compactionThreshold)}</span>
                </div>
              );
            })()}

            {/* Lead agent model badge — same metric font / color as the gauge so
                eye reads "info strip" not a separate widget. Sourced from /meta
                (lead_agent's MD frontmatter). @lg threshold = 32rem (512px)
                composer width: at that point left tools (~104px) + gauge (~80px)
                + badge (~140px) + send (32px) + gaps + padding all comfortably
                fit; below that the badge is the first to fold (gauge survives
                down to @sm). truncate at max-w prevents a very long identifier
                from pushing send off-screen even at @lg. Skipped entirely while
                config is still loading (best-effort fail). */}
            {leadAgentModel && (
              <span
                className="hidden @lg:inline-flex h-8 items-center font-mono text-xs text-text-tertiary dark:text-text-tertiary-dark select-none truncate max-w-[140px] translate-y-[0.5px]"
                title={`Lead agent 当前模型：${leadAgentModel}`}
              >
                {leadAgentModel}
              </span>
            )}

            {/* Unified Send / Stop / Cancelling / Inject button */}
            {(() => {
              const isStop = isStreaming && !content.trim() && !cancelling;
              // A queued turn can be neither stopped nor injected into until it
              // starts running; disable the button so the click doesn't 409 into
              // a silent no-op. Re-enables when agent_start clears queuedInfo.
              const queued = queuedInfo !== null;
              const sendDisabled =
                (!isStreaming && !content.trim() && !hasStaged && !effectiveForceCompact) || cancelling || sending || queued;
              return (
                <button
                  onClick={handleSend}
                  disabled={sendDisabled}
                  className={`w-8 h-8 flex items-center justify-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    isStop || cancelling
                      ? 'bg-red-500 text-white hover:bg-red-600'
                      : 'bg-accent text-white hover:bg-accent-hover'
                  }`}
                  aria-label={
                    queued ? 'Queued' : cancelling ? 'Cancelling' : sending ? 'Sending' : isStop ? 'Stop generation' : isStreaming ? 'Inject message' : 'Send message'
                  }
                  title={queued ? '排队中，开始运行后可操作' : cancelling ? '正在停止…' : sending ? '发送中…' : isStop ? '停止生成' : isStreaming ? '追加指令' : '发送消息'}
                >
                  {cancelling || sending ? (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="animate-spin">
                      <path d="M21 12a9 9 0 1 1-6.219-8.56" strokeLinecap="round" />
                    </svg>
                  ) : isStop ? (
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                      <rect x="4" y="4" width="8" height="8" rx="1" />
                    </svg>
                  ) : (
                    <svg
                      width="16"
                      height="16"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.75"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M12 19V5M5 12l7-7 7 7" />
                    </svg>
                  )}
                </button>
              );
            })()}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
