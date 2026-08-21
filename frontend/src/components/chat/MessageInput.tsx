'use client';

import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { useChat } from '@/features/chat/runtime/useChat';
import { useComposerSend } from '@/features/chat/runtime/useComposerSend';
import { useStreamStore } from '@/stores/streamStore';
import { useUIStore } from '@/stores/uiStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useConfigStore } from '@/stores/configStore';
import { useStagedFilesStore, type StagedFile } from '@/stores/stagedFilesStore';
import StagedFileChip from './StagedFileChip';
import { injectMessage, cancelExecution, getSkills, listArtifacts } from '@/lib/api';
import type { UploadEvent } from '@/lib/api';
import type { ArtifactSummary, ReferencedArtifactRef, SkillItem } from '@/types';
import { StatusNotice } from '@/components/ui/StatusNotice';
import { formatTokens } from '@/lib/formatTokens';
import { formatBytes } from '@/lib/formatBytes';
import { MAX_MESSAGE_CHARS, MAX_CHAT_ATTACHMENTS } from '@/lib/constants';
import ComposerAutocomplete, {
  type ComposerSuggestion,
} from '@/features/chat/composer/ComposerAutocomplete';
import {
  composerTriggerKey,
  findComposerTrigger,
  matchesComposerQuery,
  removeComposerTrigger,
  shouldCommitComposerSelection,
} from '@/features/chat/composer/composerTrigger';

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

// Stable empty ref for the absent-draft case, so the files selector doesn't
// return a fresh [] each render (which would thrash zustand's equality check).
const EMPTY_FILES: StagedFile[] = [];

export default function MessageInput() {
  // Composer text + files live in the draft store keyed by conversation, not in
  // local state: switching conversations flips currentLoading, which unmounts
  // this component (the loading placeholder), so local state can't survive a
  // switch. The active conversation is `activeKey`; its draft is drafts[activeKey]
  // (absent ⇒ blank). activeKey is also the send's OWNER key (see useComposerSend).
  const activeKey = useStagedFilesStore((s) => s.activeKey);
  const content = useStagedFilesStore((s) => s.drafts[s.activeKey]?.text ?? '');
  const setText = useStagedFilesStore((s) => s.setText);
  // Armed by the "compact" toggle; rides the next send as force_compact and is
  // cleared on a successful send. A compact-only send (no text) is allowed.
  const [forceCompact, setForceCompact] = useState(false);
  // Skill activation picker. `activeSkills` = slugs armed for the next send
  // (rides as activate_skills, cleared on success). The picker lists only ENABLED
  // skills (a skill disabled in the management page is hidden here — enabled
  // governs both the model's L1 index and this picker). Loaded lazily on first open.
  const [activeSkills, setActiveSkills] = useState<string[]>([]);
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const [enabledSkills, setEnabledSkills] = useState<SkillItem[]>([]);
  const [skillsLoaded, setSkillsLoaded] = useState(false);
  const [skillsError, setSkillsError] = useState(false);
  // Existing user-upload artifacts explicitly referenced for the next send.
  // These are per-turn (unlike sticky skill activation) and are resolved by id
  // again at the backend admission boundary.
  const [referencedArtifacts, setReferencedArtifacts] = useState<ReferencedArtifactRef[]>([]);
  const [referenceCandidates, setReferenceCandidates] = useState<ArtifactSummary[]>([]);
  const [referencesLoaded, setReferencesLoaded] = useState(false);
  const [referencesError, setReferencesError] = useState(false);
  const [caretPosition, setCaretPosition] = useState(0);
  const [compositionActive, setCompositionActive] = useState(false);
  const [dismissedTriggerKey, setDismissedTriggerKey] = useState<string | null>(null);
  const [autocompleteIndex, setAutocompleteIndex] = useState(0);
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
  const addPendingInject = useStreamStore((s) => s.addPendingInject);
  const removePendingInject = useStreamStore((s) => s.removePendingInject);
  // QUEUED marker: set on the execution_queued SSE event, cleared on the first
  // agent_start (turn started RUNNING) / endStream / reset. While set, the turn
  // is parked in a worker-local concurrency semaphore and is neither cancellable
  // nor injectable — both endpoints gate on the engine being interactive (RUNNING)
  // and 409 otherwise. We use it to disable the composer action button so it
  // doesn't silently no-op during the wait.
  const queuedInfo = useStreamStore((s) => s.queuedInfo);
  const toggleArtifactPanel = useUIStore((s) => s.toggleArtifactPanel);
  const composerFocusRequestId = useUIStore((s) => s.composerFocusRequestId);
  const composerFocusConsumedId = useUIStore((s) => s.composerFocusConsumedId);
  const consumeComposerFocusRequest = useUIStore((s) => s.consumeComposerFocusRequest);

  const stagedFiles = useStagedFilesStore((s) => s.drafts[s.activeKey]?.files ?? EMPTY_FILES);
  const addFiles = useStagedFilesStore((s) => s.addFiles);
  const removeFile = useStagedFilesStore((s) => s.removeFile);
  const stageNotice = useStagedFilesStore((s) => s.notice);
  const dismissNotice = useStagedFilesStore((s) => s.dismissNotice);

  // The send lifecycle (lock → clear → await) for both send and inject lives in
  // this hook (single enforcement point); see useComposerSend. It's OWNER-keyed on
  // activeKey: the send clears THAT conversation's draft (text + the files that
  // ride the POST) at send start, so it stays correct even if the user navigates
  // mid-send. A failed send is a best-effort loss — there is no restore.
  const { sending, submit, inject } = useComposerSend(activeKey, content, stagedFiles);

  useEffect(() => {
    if (composerFocusRequestId <= composerFocusConsumedId) return;
    const raf = requestAnimationFrame(() => {
      textareaRef.current?.focus();
      consumeComposerFocusRequest(composerFocusRequestId);
    });
    return () => cancelAnimationFrame(raf);
  }, [composerFocusRequestId, composerFocusConsumedId, consumeComposerFocusRequest]);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [content]);

  const conversationId = useConversationStore((s) => s.current?.id);
  const streamConversationId = useStreamStore((s) => s.conversationId);

  const rawComposerTrigger = !isStreaming && !compositionActive
    ? findComposerTrigger(content, caretPosition)
    : null;
  const rawTriggerKey = rawComposerTrigger
    ? composerTriggerKey(rawComposerTrigger)
    : null;
  const composerTrigger = rawComposerTrigger && rawTriggerKey !== dismissedTriggerKey
    ? rawComposerTrigger
    : null;
  const skillAutocompleteOpen = composerTrigger?.kind === 'skill';
  const fileAutocompleteOpen = composerTrigger?.kind === 'file';

  // Context-usage gauge: how much context the next message will carry, vs the
  // backend auto-compaction threshold. Sourced from the persisted branch tail's
  // `execution_metrics` — the last lead LLM call's `last_input_tokens +
  // last_output_tokens`, matching the compaction trigger (input+output >
  // threshold) so the gauge and the model-facing <context_usage> warning read
  // the same number. If the turn ended on a compaction (triggered on the final
  // response, no further lead call), the backend overrides last_input_tokens
  // with the summary size and zeroes last_output_tokens, so the gauge correctly
  // drops post-compaction. lead-only by convention; subagent compaction does
  // not pollute these fields. See the engine state contract in src/core/execution/engine.py.
  // Non-live: updates after each completed turn / on conversation load.
  const branchPath = useConversationStore((s) => s.branchPath);
  const compactionThreshold = useConfigStore((s) => s.compactionThreshold);
  const leadAgentModel = useConfigStore((s) => s.leadAgentModel);
  const lastNode = branchPath.length > 0 ? branchPath[branchPath.length - 1] : null;
  // Slugs already active on this branch (the tail message's persisted sticky list).
  // The picker marks these "已激活" so re-checking one — which re-injects its full body,
  // a deliberate "re-remind" (useful after compaction) — is an informed choice, not a
  // surprise re-send. Doesn't gate arming: the user can still re-check to re-inject.
  const alreadyActiveSkills = useMemo(
    () => new Set(lastNode?.active_skills ?? []),
    [lastNode?.active_skills],
  );
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

  // Refresh the enabled-skill list each time either entry opens — the button is
  // the browse path, while `/` filters the same candidates from the textarea.
  // (a skill just disabled in 技能管理 must not linger here), but stale-while-
  // revalidate: keep the previously-loaded list on screen and refetch silently,
  // so re-opening doesn't flash "加载中" on every open. The spinner shows only on
  // the first-ever open (no cached list). getSkills returns all visible skills;
  // the picker shows only enabled ones. On a background-refetch failure we keep
  // the stale list (render prefers a usable list over the error screen) and only
  // surface the error when there's nothing cached to show.
  useEffect(() => {
    if (!skillPickerOpen && !skillAutocompleteOpen) return;
    let alive = true;
    setSkillsError(false);
    (async () => {
      try {
        const data = await getSkills();
        if (alive) setEnabledSkills(data.skills.filter((s) => s.enabled));
      } catch (err) {
        console.error('Failed to load skills:', err);
        // Don't clear the cached list — a failed refresh keeps showing what we
        // last had (stale, but usable). The error screen only appears when we
        // have no list at all (render precedence below).
        if (alive) setSkillsError(true);
      } finally {
        if (alive) setSkillsLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [skillPickerOpen, skillAutocompleteOpen]);

  // `@` searches persisted user uploads from the whole current conversation.
  // Current-turn staged files are intentionally absent: they already ride this
  // message as attachments and only become reference candidates after flush.
  useEffect(() => {
    if (!fileAutocompleteOpen) return;
    if (!conversationId) {
      setReferenceCandidates([]);
      setReferencesLoaded(true);
      setReferencesError(false);
      return;
    }
    let alive = true;
    setReferencesError(false);
    (async () => {
      try {
        const data = await listArtifacts(conversationId);
        if (!alive) return;
        setReferenceCandidates(
          data.artifacts
            .filter((artifact) => artifact.source === 'user_upload')
            .sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
        );
      } catch (err) {
        console.error('Failed to load reference candidates:', err);
        if (alive) setReferencesError(true);
      } finally {
        if (alive) setReferencesLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [fileAutocompleteOpen, conversationId]);

  const skillSuggestions = useMemo<ComposerSuggestion[]>(() => enabledSkills.map((skill) => ({
    key: `skill:${skill.slug}`,
    title: skill.name,
    description: skill.description,
    badge: alreadyActiveSkills.has(skill.slug) ? '已激活' : undefined,
    selected: activeSkills.includes(skill.slug),
  })), [enabledSkills, alreadyActiveSkills, activeSkills]);

  const autocompleteSuggestions = useMemo<ComposerSuggestion[]>(() => {
    if (!composerTrigger) return [];
    if (composerTrigger.kind === 'skill') {
      return skillSuggestions.filter((suggestion) => matchesComposerQuery(
        composerTrigger.query,
        suggestion.title,
        suggestion.description,
        suggestion.key.slice('skill:'.length),
      ));
    }
    return referenceCandidates
      .filter((artifact) => matchesComposerQuery(
        composerTrigger.query,
        artifact.original_filename,
        artifact.title,
        artifact.id,
      ))
      .map((artifact) => ({
        key: `file:${artifact.id}`,
        title: artifact.original_filename || artifact.title,
        description: '当前会话已上传文件',
        selected: referencedArtifacts.some((item) => item.id === artifact.id),
      }));
  }, [
    composerTrigger,
    skillSuggestions,
    referenceCandidates,
    referencedArtifacts,
  ]);

  const autocompleteSuggestionKey = autocompleteSuggestions
    .map((suggestion) => suggestion.key)
    .join('|');
  useEffect(() => {
    setAutocompleteIndex(0);
  }, [composerTrigger?.kind, composerTrigger?.query, autocompleteSuggestionKey, skillPickerOpen]);

  // Armed skills belong to the conversation they were armed in — clear on any
  // conversation change, 新建对话 included (#2). Switching to an *existing*
  // conversation remounts this component (currentLoading flips), but 新建对话 keeps
  // it mounted, so without this the chips would leak onto the new chat's first
  // message. Mirrors forceCompact's hygiene (which is gated by hasPersistedHistory).
  useEffect(() => {
    setActiveSkills([]);
    setReferencedArtifacts([]);
    setReferenceCandidates([]);
    setReferencesLoaded(false);
    setReferencesError(false);
    setSkillPickerOpen(false);
    setDismissedTriggerKey(null);
  }, [activeKey]);

  const toggleSkill = useCallback((slug: string) => {
    setActiveSkills((prev) =>
      prev.includes(slug) ? prev.filter((s) => s !== slug) : [...prev, slug],
    );
  }, []);

  const removeReference = useCallback((artifactId: string) => {
    setReferencedArtifacts((prev) => prev.filter((item) => item.id !== artifactId));
  }, []);

  const commitComposerSelection = useCallback(() => {
    if (!composerTrigger) return;
    const next = removeComposerTrigger(content, composerTrigger);
    setText(next.text);
    setCaretPosition(next.caret);
    setDismissedTriggerKey(null);
    setSkillPickerOpen(false);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (!textarea) return;
      textarea.focus();
      textarea.setSelectionRange(next.caret, next.caret);
    });
  }, [composerTrigger, content, setText]);

  const selectAutocompleteSuggestion = useCallback((suggestion: ComposerSuggestion) => {
    if (suggestion.key.startsWith('skill:')) {
      const slug = suggestion.key.slice('skill:'.length);
      setActiveSkills((prev) => prev.includes(slug) ? prev : [...prev, slug]);
      commitComposerSelection();
      return;
    }

    const artifactId = suggestion.key.slice('file:'.length);
    const artifact = referenceCandidates.find((candidate) => candidate.id === artifactId);
    if (!artifact) return;
    setReferencedArtifacts((prev) => {
      if (prev.some((item) => item.id === artifact.id)) return prev;
      if (prev.length >= MAX_CHAT_ATTACHMENTS) return prev;
      return [
        ...prev,
        {
          id: artifact.id,
          filename: artifact.original_filename || artifact.title,
        },
      ];
    });
    commitComposerSelection();
  }, [commitComposerSelection, referenceCandidates]);

  const selectSkillPickerSuggestion = useCallback((suggestion: ComposerSuggestion) => {
    if (!suggestion.key.startsWith('skill:')) return;
    toggleSkill(suggestion.key.slice('skill:'.length));
  }, [toggleSkill]);

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
      // in-flight turn). The hook owns the empty-guard / lock / clear-on-send.
      const convId = streamConversationId || conversationId;
      if (!convId) return;
      await inject(async (text) => {
        const pendingId = addPendingInject(text);
        try {
          await injectMessage(convId, text);
        } catch (err) {
          removePendingInject(pendingId);
          throw err;
        }
      });
      return;
    }

    // New-message send: text and/or staged attachments ride one POST. When the
    // compact toggle is armed AND there's history to compact, force_compact
    // rides along (and allowEmpty lets a compact-only send through). Clear the
    // raw `forceCompact` on any successful armed send, even if it didn't take
    // effect this turn (state hygiene — don't leave stale armed state behind).
    const compact = effectiveForceCompact;
    // Snapshot armed skills for this send (cleared on success below). An
    // activation-only send (no text/files) is allowed, same as compact.
    const skillsToActivate = activeSkills;
    const referencesToUse = referencedArtifacts;
    const skillRefsToActivate = skillsToActivate.map((slug) => {
      const info = enabledSkills.find((skill) => skill.slug === slug);
      return { slug, name: info?.name ?? slug };
    });
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
        const ok = await sendMessage(
          text, undefined, files, compact, onUpload,
          skillRefsToActivate.length ? skillRefsToActivate : undefined,
          referencesToUse.length ? referencesToUse : undefined,
        );
        if (ok && forceCompact) setForceCompact(false);
        if (ok && skillsToActivate.length) setActiveSkills([]);
        if (ok && referencesToUse.length) setReferencedArtifacts([]);
        return ok;
      } finally {
        // Single convergence point: success / failure / throw all clear the
        // bar. The error itself is already surfaced via streamStore.setError
        // by useChat.sendMessage; composer state (text + chips) is preserved
        // by useComposerSend's reconcile-on-success rule.
        setUploadProgress(null);
      }
    }, compact || skillsToActivate.length > 0 || referencesToUse.length > 0);
  }, [content, isStreaming, cancelling, setCancelling, conversationId, streamConversationId, inject, submit, sendMessage, forceCompact, effectiveForceCompact, activeSkills, referencedArtifacts, enabledSkills, addPendingInject, removePendingInject]);

  const handleCompositionStart = useCallback(() => {
    isComposingRef.current = true;
    setCompositionActive(true);
  }, []);

  const handleCompositionEnd = useCallback(() => {
    // Chrome fires compositionend BEFORE keydown, so delay the reset
    // to ensure the Enter keydown that confirms composition is still blocked
    requestAnimationFrame(() => {
      isComposingRef.current = false;
      setCompositionActive(false);
    });
  }, []);

  const handleTextChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    setCaretPosition(e.target.selectionStart ?? e.target.value.length);
    setDismissedTriggerKey(null);
    setSkillPickerOpen(false);
  }, [setText]);

  const handleTextSelection = useCallback((e: React.SyntheticEvent<HTMLTextAreaElement>) => {
    const textarea = e.currentTarget;
    setCaretPosition(textarea.selectionStart ?? textarea.value.length);
    setDismissedTriggerKey(null);
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (composerTrigger) {
        if (e.key === 'ArrowDown' && autocompleteSuggestions.length > 0) {
          e.preventDefault();
          setAutocompleteIndex((index) => (index + 1) % autocompleteSuggestions.length);
          return;
        }
        if (e.key === 'ArrowUp' && autocompleteSuggestions.length > 0) {
          e.preventDefault();
          setAutocompleteIndex((index) =>
            (index - 1 + autocompleteSuggestions.length) % autocompleteSuggestions.length,
          );
          return;
        }
        if (shouldCommitComposerSelection(e.key, {
          shiftKey: e.shiftKey,
          isComposing: isComposingRef.current,
          suggestionCount: autocompleteSuggestions.length,
        })) {
          e.preventDefault();
          selectAutocompleteSuggestion(
            autocompleteSuggestions[Math.min(autocompleteIndex, autocompleteSuggestions.length - 1)],
          );
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          if (rawTriggerKey) setDismissedTriggerKey(rawTriggerKey);
          return;
        }
      }
      if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
        e.preventDefault();
        handleSend();
      }
    },
    [composerTrigger, autocompleteSuggestions, autocompleteIndex, selectAutocompleteSuggestion, rawTriggerKey, handleSend]
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
    <div className="relative px-4 pt-4 pb-[calc(1.25rem+env(safe-area-inset-bottom))]">
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
          className="relative @container bg-surface dark:bg-surface-dark border border-border dark:border-border-dark focus-within:border-accent dark:focus-within:border-accent rounded-2xl shadow-float px-4 py-3 transition-colors"
        >
          {skillPickerOpen && (
            <button
              className="fixed inset-0 z-20 cursor-default"
              aria-label="关闭技能选择"
              onClick={() => setSkillPickerOpen(false)}
            />
          )}
          {(composerTrigger || skillPickerOpen) && (
            <ComposerAutocomplete
              kind={composerTrigger?.kind ?? 'skill'}
              suggestions={composerTrigger ? autocompleteSuggestions : skillSuggestions}
              activeIndex={autocompleteIndex}
              loading={
                (composerTrigger?.kind ?? 'skill') === 'skill'
                  ? !skillsLoaded
                  : !referencesLoaded
              }
              error={
                (composerTrigger?.kind ?? 'skill') === 'skill'
                  ? skillsError
                  : referencesError
              }
              hasConversation={Boolean(conversationId)}
              hint={skillPickerOpen ? '可多选 · 输入 / 可搜索' : undefined}
              emptyText={skillPickerOpen ? '暂无可激活的技能，可在「技能管理」中开启。' : undefined}
              multiSelect={skillPickerOpen}
              onActiveIndexChange={setAutocompleteIndex}
              onSelect={skillPickerOpen ? selectSkillPickerSuggestion : selectAutocompleteSuggestion}
            />
          )}
          {/* Why some picked files weren't staged (unsupported format / over
              the attachment cap). Covers drag-drop too, which bypasses the
              disabled attach button. */}
          {stageNotice && (
            <StatusNotice
              tone="warning"
              onDismiss={dismissNotice}
              dismissLabel="关闭附件提示"
              className="mb-2"
            >
              <div className="space-y-0.5">
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
            </StatusNotice>
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

          {/* Existing uploads from this conversation, explicitly referenced for
              the next turn via @. Distinct from staged files: no bytes are sent. */}
          {referencedArtifacts.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {referencedArtifacts.map((artifact) => (
                <span
                  key={artifact.id}
                  className="inline-flex min-w-0 items-center gap-1 max-w-[16rem] pl-2 pr-1 py-1 rounded-lg bg-surface dark:bg-bg-dark border border-border dark:border-border-dark text-xs text-text-secondary dark:text-text-secondary-dark"
                  title={`引用会话文件：${artifact.filename}`}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                    <path d="M10 13a5 5 0 0 0 7.1.1l2-2a5 5 0 0 0-7.1-7.1l-1.1 1.1" />
                    <path d="M14 11a5 5 0 0 0-7.1-.1l-2 2A5 5 0 0 0 12 20l1.1-1.1" />
                  </svg>
                  <span className="shrink-0 text-text-tertiary dark:text-text-tertiary-dark">引用</span>
                  <span className="min-w-0 truncate">{artifact.filename}</span>
                  <button
                    onClick={() => removeReference(artifact.id)}
                    className="shrink-0 p-0.5 rounded hover:bg-bg dark:hover:bg-surface-dark"
                    aria-label={`取消引用 ${artifact.filename}`}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                      <path d="M18 6L6 18M6 6l12 12" />
                    </svg>
                  </button>
                </span>
              ))}
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
                  <line x1="14" y1="10" x2="21" y2="3" />
                  <line x1="3" y1="21" x2="10" y2="14" />
                </svg>
                <span>发送后压缩一次上下文</span>
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

          {/* Armed-skill chips — the next send will activate these skills. */}
          {activeSkills.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {activeSkills.map((slug) => {
                const info = enabledSkills.find((s) => s.slug === slug);
                return (
                  <span
                    key={slug}
                    className="inline-flex items-center gap-1 pl-2 pr-1 py-1 rounded-lg bg-accent/10 border border-accent/40 text-xs text-accent"
                  >
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                      <path d="M6.5 2l1 2.7 2.7 1-2.7 1-1 2.7-1-2.7-2.7-1 2.7-1z" />
                      <path d="M11.5 9.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z" />
                    </svg>
                    <span>{info?.name ?? slug}</span>
                    <button
                      onClick={() => toggleSkill(slug)}
                      className="shrink-0 p-0.5 rounded hover:bg-accent/20"
                      aria-label={`取消激活 ${info?.name ?? slug}`}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </span>
                );
              })}
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
            onChange={handleTextChange}
            onSelect={handleTextSelection}
            onClick={handleTextSelection}
            onBlur={() => {
              if (rawTriggerKey) setDismissedTriggerKey(rawTriggerKey);
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onCompositionStart={handleCompositionStart}
            onCompositionEnd={handleCompositionEnd}
            maxLength={MAX_MESSAGE_CHARS}
            placeholder={
              isStreaming
                ? '输入追加指令，按 Enter 发送…'
                : isNewConversation
                  ? '开始新的对话，/ 选择技能…'
                  : '输入消息，@ 引用文件，/ 选择技能…'
            }
            role="combobox"
            aria-autocomplete="list"
            aria-expanded={Boolean(composerTrigger)}
            aria-controls={composerTrigger ? 'composer-autocomplete-list' : undefined}
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
                className="h-11 w-11 sm:h-8 sm:w-8 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
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
                className="h-11 w-11 sm:h-8 sm:w-8 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
                aria-label="Toggle artifact panel"
                title="切换文件面板"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="1.5" y="2" width="13" height="12" rx="1.5" />
                  <path d="M9.5 2v12" />
                </svg>
              </button>

              {/* Skill activation picker — arms skills for the next send. Disabled
                  while streaming (activation rides a fresh turn). */}
              <div className="relative">
                <button
                  onClick={() => {
                    if (rawTriggerKey) setDismissedTriggerKey(rawTriggerKey);
                    setSkillPickerOpen((v) => !v);
                  }}
                  disabled={isStreaming}
                  className={`h-11 w-11 sm:h-8 sm:w-8 flex items-center justify-center rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    activeSkills.length > 0 || skillPickerOpen
                      ? 'bg-accent/15 text-accent'
                      : 'text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark'
                  }`}
                  aria-label="激活技能"
                  aria-pressed={skillPickerOpen}
                  aria-expanded={skillPickerOpen}
                  aria-controls={skillPickerOpen ? 'composer-autocomplete-list' : undefined}
                  title="激活技能：让本轮应用某个技能的指令"
                >
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M6.5 2l1 2.7 2.7 1-2.7 1-1 2.7-1-2.7-2.7-1 2.7-1z" />
                    <path d="M11.5 9.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z" />
                  </svg>
                </button>

              </div>

              {/* Compact context — arms a one-shot compaction on the next send.
                  Disabled while streaming (compaction rides a fresh turn, and the
                  composer can't start one mid-stream). */}
              <button
                onClick={() => setForceCompact((v) => !v)}
                disabled={isStreaming || !hasPersistedHistory}
                className={`h-11 w-11 sm:h-8 sm:w-8 flex items-center justify-center rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                  effectiveForceCompact
                    ? 'bg-accent/15 text-accent'
                    : 'text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark'
                }`}
                aria-label="压缩上下文"
                aria-pressed={effectiveForceCompact}
                title={
                  !hasPersistedHistory
                    ? '当前会话无历史可压缩'
                    : effectiveForceCompact
                      ? '已开启：下次发送后，自动把已有对话整理成摘要（点击取消）'
                      : '下次发送后，自动把已有对话整理成摘要'
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
                <span className="hidden @sm:inline ml-1 text-xs tabular-nums text-text-tertiary dark:text-text-tertiary-dark">
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
                      className={near ? 'text-status-warning' : 'text-accent'}
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
                (!isStreaming && !content.trim() && !hasStaged && !effectiveForceCompact && activeSkills.length === 0 && referencedArtifacts.length === 0) || cancelling || sending || queued;
              return (
                <button
                  onClick={handleSend}
                  disabled={sendDisabled}
                  className={`w-11 h-11 sm:w-8 sm:h-8 flex items-center justify-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    isStop || cancelling
                      ? 'bg-status-error text-white hover:bg-status-error/80'
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
