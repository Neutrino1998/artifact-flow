import { create } from 'zustand';
import type { ArtifactSummary, ArtifactDetail, VersionSummary, VersionDetail } from '@/types';
import type { ArtifactCreatedData, ArtifactUpdatedData } from '@/types/events';

export type ArtifactViewMode = 'preview' | 'source' | 'diff';

/** Live (in-turn) content for one artifact, reduced from ARTIFACT_* SSE events.
 *  This is the source of truth for the panel DURING a turn — REST GET is now
 *  pure-DB (no overlay) and lags live. Cleared + replaced by the DB re-pull on
 *  COMPLETE (the single alignment point). `omitted` = content exceeded the live
 *  cap server-side; show stale + wait for the COMPLETE re-pull. */
export interface LiveArtifact {
  content: string;
  version: number;
  contentType: string;
  title: string;
  source: string | null;
  omitted: boolean;
  // user_upload only: original file name, for correlating to the staged File
  // (local render before the blob is flushed). null for model-created artifacts.
  originalFilename: string | null;
}

/** Apply an authoritative span delta (from compute_update): replace
 *  [offset, offset+deleted_len) with inserted_text. */
function applySpanDelta(
  content: string,
  delta: { offset: number; deleted_len: number; inserted_text: string }
): string {
  return (
    content.slice(0, delta.offset) +
    delta.inserted_text +
    content.slice(delta.offset + delta.deleted_len)
  );
}

/** Build a panel-ready ArtifactDetail from live state. versions=[] is fine:
 *  the toolbar hides the version selector while streaming (decision 6). */
function liveToDetail(id: string, live: LiveArtifact, sessionId: string | null): ArtifactDetail {
  return {
    id,
    session_id: sessionId ?? '',
    content_type: live.contentType,
    title: live.title,
    content: live.content,
    current_version: live.version,
    source: live.source,
    original_filename: live.originalFilename,
    created_at: '',
    updated_at: '',
    versions: [],
  };
}

interface ArtifactState {
  // Session context (set during streaming when conversation store may not have it yet)
  sessionId: string | null;

  // List
  artifacts: ArtifactSummary[];
  artifactsLoading: boolean;

  // Current artifact
  current: ArtifactDetail | null;
  currentLoading: boolean;

  // True iff `current` was placed there by the SSE auto-open path (i.e. the
  // agent updated an artifact mid-stream). Cleared the moment the user makes
  // any explicit pick or the panel is reset to list view. Two consumers:
  //   - useSSE auto-open: only allows the panel to switch between artifacts
  //     if the existing current was also auto-set (autoSelected=true) — never
  //     yanks a user away from an artifact they actively picked.
  //   - useSSE refreshAfterComplete: at stream end, reverts to list view only
  //     if current is auto-set; user-picked stays put with refreshed content.
  autoSelected: boolean;

  // Versions
  versions: VersionSummary[];
  selectedVersion: VersionDetail | null;

  // Diff base (previous version content for computing diff)
  diffBaseContent: string | null;

  // View
  viewMode: ArtifactViewMode;

  // Pending updates from streaming
  pendingUpdates: string[];

  // Live (in-turn) content reduced from ARTIFACT_* events, keyed by artifact id.
  liveContent: Record<string, LiveArtifact>;

  // Send-local image preview cache, keyed by upload filename → the File the user
  // just sent. Display-only and wholly separate from the composer draft (which is
  // cleared on send): it lets ImagePreview show an uploaded image instantly for
  // the live-this-turn window, before the blob is flushed and /raw works. Shares
  // liveContent's exact lifecycle — cleared at COMPLETE (clearLiveContent) and on
  // nav (reset) — so a later turn's same-named upload can't shadow it.
  localPreviews: Record<string, File>;

  // Upload state
  uploading: boolean;
  uploadError: string | null;

  // Actions
  setSessionId: (sessionId: string | null) => void;
  setArtifacts: (artifacts: ArtifactSummary[]) => void;
  setArtifactsLoading: (loading: boolean) => void;
  setCurrent: (artifact: ArtifactDetail | null) => void;
  setCurrentAuto: (artifact: ArtifactDetail) => void;
  refreshCurrent: (artifact: ArtifactDetail) => void;
  setCurrentLoading: (loading: boolean) => void;
  setVersions: (versions: VersionSummary[]) => void;
  setSelectedVersion: (version: VersionDetail | null) => void;
  setDiffBaseContent: (content: string | null) => void;
  setViewMode: (mode: ArtifactViewMode) => void;
  addPendingUpdate: (identifier: string) => void;
  clearPendingUpdates: () => void;
  applyArtifactCreated: (data: ArtifactCreatedData) => void;
  applyArtifactUpdated: (data: ArtifactUpdatedData) => void;
  /** Open an artifact from live (in-turn) content if we have it. Returns true
   *  when handled (caller should skip the REST fetch — REST is pure-DB and would
   *  show stale content for an artifact edited this turn). User-picked → not auto. */
  selectFromLive: (id: string) => boolean;
  clearLiveContent: () => void;
  /** Stash the just-sent images (filtered from a send's files) as send-local
   *  previews. Non-images are ignored (nothing reads them). */
  setLocalPreviews: (files: File[]) => void;
  setUploading: (uploading: boolean) => void;
  setUploadError: (error: string | null) => void;
  reset: () => void;
}

function defaultViewMode(contentType?: string): ArtifactViewMode {
  if (contentType === 'text/markdown') return 'preview';
  if (contentType?.startsWith('image/')) return 'preview';  // 图片走 ImagePreview
  return 'source';
}

export const useArtifactStore = create<ArtifactState>((set, get) => ({
  sessionId: null,

  artifacts: [],
  artifactsLoading: false,

  current: null,
  currentLoading: false,
  autoSelected: false,

  versions: [],
  selectedVersion: null,
  diffBaseContent: null,

  viewMode: 'preview',

  pendingUpdates: [],
  liveContent: {},
  localPreviews: {},

  uploading: false,
  uploadError: null,

  setSessionId: (sessionId) => set({ sessionId }),
  setArtifacts: (artifacts) => set({ artifacts }),
  setArtifactsLoading: (loading) => set({ artifactsLoading: loading }),
  setCurrent: (artifact) =>
    set({
      current: artifact,
      autoSelected: false,
      viewMode: artifact ? defaultViewMode(artifact.content_type) : 'preview',
    }),
  setCurrentAuto: (artifact) =>
    set({
      current: artifact,
      autoSelected: true,
      viewMode: defaultViewMode(artifact.content_type),
    }),
  // Same-artifact content refresh: write the new ArtifactDetail through
  // WITHOUT touching `autoSelected` or `viewMode`. Used when a stream
  // updates the artifact the user currently has open — we must not flip
  // ownership back to "auto-selected" (which would yank the user to the
  // list at stream end) and must not reset their chosen view mode (diff,
  // source, etc). Guarded against accidental cross-id misuse.
  refreshCurrent: (artifact) =>
    set((s) =>
      s.current && s.current.id === artifact.id ? { current: artifact } : s
    ),
  setCurrentLoading: (loading) => set({ currentLoading: loading }),
  setVersions: (versions) => set({ versions }),
  setSelectedVersion: (version) => set({ selectedVersion: version }),
  setDiffBaseContent: (content) => set({ diffBaseContent: content }),
  setViewMode: (mode) => set({ viewMode: mode }),
  addPendingUpdate: (identifier) =>
    set((s) => ({
      pendingUpdates: s.pendingUpdates.includes(identifier)
        ? s.pendingUpdates
        : [...s.pendingUpdates, identifier],
    })),
  clearPendingUpdates: () => set({ pendingUpdates: [] }),

  // ARTIFACT_CREATED: a new artifact appeared this turn. REST list no longer
  // surfaces unflushed artifacts (overlay removed), so we upsert it into the
  // list FROM the event. Auto-open it unless the user has actively picked
  // another artifact, mirroring the old tool-completion behavior — except
  // tool-persisted outputs (source='tool') only list, never grab the panel.
  applyArtifactCreated: (d) =>
    set((s) => {
      const live: LiveArtifact = {
        content: d.content ?? '',
        version: d.current_version,
        contentType: d.content_type,
        title: d.title,
        source: d.source,
        omitted: !!d.content_omitted,
        originalFilename: d.original_filename ?? null,
      };
      const liveContent = { ...s.liveContent, [d.id]: live };
      const exists = s.artifacts.some((a) => a.id === d.id);
      const summary: ArtifactSummary = {
        id: d.id,
        content_type: d.content_type,
        title: d.title,
        current_version: d.current_version,
        source: d.source,
        original_filename: d.original_filename ?? null,
        created_at: '',
        updated_at: '',
      };
      const artifacts = exists
        ? s.artifacts.map((a) =>
            a.id === d.id ? { ...a, title: d.title, current_version: d.current_version } : a
          )
        : [...s.artifacts, summary];
      const pendingUpdates = s.pendingUpdates.includes(d.id)
        ? s.pendingUpdates
        : [...s.pendingUpdates, d.id];

      const next: Partial<ArtifactState> = { liveContent, artifacts, pendingUpdates };
      const autoOpen = d.source !== 'tool';
      if (autoOpen && (!s.current || s.autoSelected)) {
        next.current = liveToDetail(d.id, live, s.sessionId);
        next.autoSelected = true;
        next.viewMode = defaultViewMode(d.content_type);
        next.versions = [];
        next.selectedVersion = null;
      } else if (s.current && s.current.id === d.id) {
        next.current = liveToDetail(d.id, live, s.sessionId);
      }
      return next;
    }),

  // ARTIFACT_UPDATED: rewrite (full content) or targeted update (span delta).
  // Apply onto the live base (the backend guarantees a full-content event for
  // an artifact precedes any delta this turn, so a delta always has a base).
  applyArtifactUpdated: (d) =>
    set((s) => {
      const base = s.liveContent[d.id];
      let content: string;
      let omitted = false;
      if (d.delta && base && !base.omitted) {
        content = applySpanDelta(base.content, d.delta);
      } else if (typeof d.content === 'string') {
        content = d.content;
      } else if (d.content_omitted) {
        // oversized full-content event: keep stale base, flag for COMPLETE re-pull
        content = base?.content ?? '';
        omitted = true;
      } else {
        // delta with no base (e.g. missed the full event on reconnect): can't
        // apply; keep base and rely on the COMPLETE DB re-pull. Still dot it.
        content = base?.content ?? '';
        omitted = base?.omitted ?? true;
      }
      const live: LiveArtifact = {
        content,
        version: d.current_version,
        contentType: base?.contentType ?? 'text/markdown',
        title: base?.title ?? d.id,
        source: base?.source ?? 'agent',
        omitted,
        originalFilename: base?.originalFilename ?? null,
      };
      const liveContent = { ...s.liveContent, [d.id]: live };
      const artifacts = s.artifacts.map((a) =>
        a.id === d.id ? { ...a, current_version: d.current_version } : a
      );
      const pendingUpdates = s.pendingUpdates.includes(d.id)
        ? s.pendingUpdates
        : [...s.pendingUpdates, d.id];

      const next: Partial<ArtifactState> = { liveContent, artifacts, pendingUpdates };
      if (s.current && s.current.id === d.id) {
        // keep the user's view mode / selection ownership; just refresh content
        next.current = { ...s.current, content: live.content, current_version: live.version };
      } else if (!s.current || s.autoSelected) {
        next.current = liveToDetail(d.id, live, s.sessionId);
        next.autoSelected = true;
        next.viewMode = defaultViewMode(live.contentType);
        next.versions = [];
        next.selectedVersion = null;
      }
      return next;
    }),

  selectFromLive: (id) => {
    const live = get().liveContent[id];
    if (!live || live.omitted) return false;
    set({
      current: liveToDetail(id, live, get().sessionId),
      autoSelected: false, // user-picked: keep them here at COMPLETE
      viewMode: defaultViewMode(live.contentType),
      versions: [],
      selectedVersion: null,
      diffBaseContent: null,
    });
    return true;
  },

  // Cleared together with liveContent at COMPLETE: the live-this-turn window is
  // over, so the local previews are no longer needed (settled artifacts read /raw).
  clearLiveContent: () => set({ liveContent: {}, localPreviews: {} }),

  setLocalPreviews: (files) =>
    set((s) => {
      const next = { ...s.localPreviews };
      for (const f of files) {
        if (f.type.startsWith('image/')) next[f.name] = f;
      }
      return { localPreviews: next };
    }),

  setUploading: (uploading) => set({ uploading }),
  setUploadError: (error) => set({ uploadError: error }),
  reset: () =>
    set({
      sessionId: null,
      artifacts: [],
      current: null,
      autoSelected: false,
      currentLoading: false,
      versions: [],
      selectedVersion: null,
      diffBaseContent: null,
      viewMode: 'preview',
      pendingUpdates: [],
      liveContent: {},
      localPreviews: {},
      uploading: false,
      uploadError: null,
    }),
}));
