import { create } from 'zustand';
import type { ArtifactSummary, ArtifactDetail, VersionSummary, VersionDetail } from '@/types';
import type { ArtifactCreatedData, ArtifactUpdatedData } from '@/types/events';
import { isCsvMime } from '@/lib/artifactPreview';

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
  // user_upload only: original file name, for correlating to the send-local
  // preview File (local render before the blob is flushed). null for model-created.
  originalFilename: string | null;
  // blob-backed (image / rich-format upload): no text content, raw via /raw.
  hasBlob: boolean;
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
 *  the toolbar hides the version selector while streaming. */
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
    has_blob: live.hasBlob,
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

  // File tabs. IDs are enough: display metadata comes from `artifacts` (or
  // `current` for a just-opened detail). Detail/version state remains scoped to
  // the single active artifact, so tabs don't duplicate the existing fetch and
  // live-update machinery.
  openArtifactIds: string[];

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
  // liveContent's exact lifecycle — cleared at COMPLETE (finishLiveTurn) and on
  // nav (reset) — so a later turn's same-named upload can't shadow it.
  localPreviews: Record<string, File>;

  // Upload state
  uploading: boolean;
  uploadError: string | null;

  // Actions
  setSessionId: (sessionId: string | null) => void;
  /** Merge a lagging DB list into the live projection without pruning live files/tabs. */
  mergeArtifactsFromDbDuringLive: (artifacts: ArtifactSummary[]) => void;
  /** Commit an authoritative terminal DB collection, pruning missing tabs. */
  reconcileArtifactsFromDb: (artifacts: ArtifactSummary[]) => void;
  /** Commit an authoritative detail 404 when the collection request is absent/late. */
  removeArtifactMissingFromDb: (artifactId: string) => void;
  setArtifactsLoading: (loading: boolean) => void;
  setCurrent: (artifact: ArtifactDetail | null) => void;
  setCurrentAuto: (artifact: ArtifactDetail) => void;
  closeArtifactTab: (artifactId: string) => void;
  refreshCurrent: (
    artifact: ArtifactDetail,
    diffBaseContent: string | undefined,
  ) => void;
  setCurrentLoading: (loading: boolean) => void;
  setVersions: (versions: VersionSummary[]) => void;
  setSelectedVersion: (version: VersionDetail | null) => void;
  setDiffBaseContent: (content: string | null) => void;
  setViewMode: (mode: ArtifactViewMode) => void;
  addPendingUpdate: (identifier: string) => void;
  /** Synchronously close the live-turn window before terminal DB awaits. */
  finishLiveTurn: () => void;
  applyArtifactCreated: (data: ArtifactCreatedData) => void;
  applyArtifactUpdated: (data: ArtifactUpdatedData) => void;
  /** Open an artifact from live (in-turn) content if we have it. Returns true
   *  when handled (caller should skip the REST fetch — REST is pure-DB and would
   *  show stale content for an artifact edited this turn). User-picked → not auto. */
  selectFromLive: (id: string) => boolean;
  /** Stash the just-sent images (filtered from a send's files) as send-local
   *  previews. Non-images are ignored (nothing reads them). */
  setLocalPreviews: (files: File[]) => void;
  setUploading: (uploading: boolean) => void;
  setUploadError: (error: string | null) => void;
  reset: () => void;
}

function defaultViewMode(contentType?: string, hasBlob?: boolean): ArtifactViewMode {
  if (contentType === 'text/markdown' || contentType === 'text/html' || isCsvMime(contentType)) return 'preview';
  if (hasBlob) return 'preview';  // 图片走 ImagePreview,其它二进制走 BinaryFilePreview
  if (contentType?.startsWith('image/')) return 'preview';
  return 'source';
}

/**
 * Reconcile every collection-shaped artifact state field from an authoritative
 * terminal DB list. Live events never use this transition: during a turn the
 * DB intentionally lags and must not prune optimistic files or tabs.
 */
function reconcileDbArtifactCollection(
  state: ArtifactState,
  artifacts: ArtifactSummary[],
): Partial<ArtifactState> {
  const persistedIds = new Set(artifacts.map((artifact) => artifact.id));
  const openArtifactIds = state.openArtifactIds.filter((id) => persistedIds.has(id));
  if (!state.current || persistedIds.has(state.current.id)) {
    return { artifacts, openArtifactIds };
  }
  return {
    artifacts,
    openArtifactIds,
    current: null,
    currentLoading: false,
    autoSelected: false,
    versions: [],
    selectedVersion: null,
    diffBaseContent: null,
    viewMode: 'preview',
  };
}

/**
 * Merge a pure-DB list into the visible in-turn projection. The DB list is a
 * complete persisted collection, but it intentionally cannot see this turn's
 * unflushed artifacts. Preserve every live entry and let its event-reduced
 * version/content metadata win over a stale row for the same ID.
 *
 * This transition deliberately does not touch tabs/current: absence from a
 * mid-stream DB response says nothing about an optimistic artifact.
 */
function mergeDbArtifactsWithLive(
  state: ArtifactState,
  dbArtifacts: ArtifactSummary[],
): ArtifactSummary[] {
  const merged = dbArtifacts.map((artifact) => {
    const live = state.liveContent[artifact.id];
    if (!live) return artifact;
    return {
      ...artifact,
      content_type: live.contentType,
      current_version: live.version,
      has_blob: live.hasBlob,
    };
  });
  const mergedIds = new Set(merged.map((artifact) => artifact.id));

  for (const [id, live] of Object.entries(state.liveContent)) {
    if (mergedIds.has(id)) continue;
    merged.push({
      id,
      content_type: live.contentType,
      title: live.title,
      current_version: live.version,
      source: live.source,
      original_filename: live.originalFilename,
      has_blob: live.hasBlob,
      created_at: '',
      updated_at: '',
    });
  }

  return merged;
}

export const useArtifactStore = create<ArtifactState>((set, get) => ({
  sessionId: null,

  artifacts: [],
  artifactsLoading: false,

  current: null,
  currentLoading: false,
  openArtifactIds: [],
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
  mergeArtifactsFromDbDuringLive: (artifacts) =>
    set((state) => ({ artifacts: mergeDbArtifactsWithLive(state, artifacts) })),
  reconcileArtifactsFromDb: (artifacts) =>
    set((state) => reconcileDbArtifactCollection(state, artifacts)),
  removeArtifactMissingFromDb: (artifactId) =>
    set((state) => {
      const artifacts = state.artifacts.filter((artifact) => artifact.id !== artifactId);
      const openArtifactIds = state.openArtifactIds.filter((id) => id !== artifactId);
      const liveContent = { ...state.liveContent };
      delete liveContent[artifactId];
      const pendingUpdates = state.pendingUpdates.filter((id) => id !== artifactId);

      if (state.current?.id !== artifactId) {
        return { artifacts, openArtifactIds, liveContent, pendingUpdates };
      }
      return {
        artifacts,
        openArtifactIds,
        liveContent,
        pendingUpdates,
        current: null,
        currentLoading: false,
        autoSelected: false,
        versions: [],
        selectedVersion: null,
        diffBaseContent: null,
        viewMode: 'preview',
      };
    }),
  setArtifactsLoading: (loading) => set({ artifactsLoading: loading }),
  setCurrent: (artifact) =>
    set((s) => ({
      current: artifact,
      autoSelected: false,
      viewMode: artifact ? defaultViewMode(artifact.content_type, artifact.has_blob) : 'preview',
      openArtifactIds:
        artifact && !s.openArtifactIds.includes(artifact.id)
          ? [...s.openArtifactIds, artifact.id]
          : s.openArtifactIds,
    })),
  setCurrentAuto: (artifact) =>
    set((s) => ({
      current: artifact,
      autoSelected: true,
      viewMode: defaultViewMode(artifact.content_type, artifact.has_blob),
      openArtifactIds: s.openArtifactIds.includes(artifact.id)
        ? s.openArtifactIds
        : [...s.openArtifactIds, artifact.id],
    })),
  closeArtifactTab: (artifactId) =>
    set((s) => {
      const openArtifactIds = s.openArtifactIds.filter((id) => id !== artifactId);
      if (s.current?.id !== artifactId) return { openArtifactIds };
      return {
        openArtifactIds,
        current: null,
        currentLoading: false,
        autoSelected: false,
        versions: [],
        selectedVersion: null,
        diffBaseContent: null,
        viewMode: 'preview',
      };
    }),
  // Passive DB reconciliation: atomically update the detail, versions and
  // diff base only while this artifact is still current. The terminal DB
  // snapshot is authoritative even when it rolls optimistic live content back
  // to a lower version after a flush failure. The caller owns the turn/request
  // generation, which excludes genuinely stale responses.
  refreshCurrent: (artifact, diffBaseContent) =>
    set((s) => {
      if (s.current?.id !== artifact.id) {
        return s;
      }

      const next: Partial<ArtifactState> = {
        current: artifact,
        versions: artifact.versions,
        selectedVersion: null,
        diffBaseContent: diffBaseContent ?? null,
      };
      // `undefined` means the DB baseline request failed. Do not keep a Diff
      // view whose old side is no longer trustworthy after reconciliation.
      if (diffBaseContent === undefined && s.viewMode === 'diff') {
        next.viewMode = defaultViewMode(artifact.content_type, artifact.has_blob);
      }
      return next;
    }),
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
  // Terminal ownership is checked by useSSE immediately before this action.
  // Keep every live-turn-only field in one synchronous transition so a new
  // turn can only append onto a clean substrate, even if DB reconciliation is
  // still awaiting network responses.
  finishLiveTurn: () =>
    set((s) => {
      const finished: Partial<ArtifactState> = {
        currentLoading: false,
        pendingUpdates: [],
        liveContent: {},
        localPreviews: {},
      };
      if (!s.autoSelected) return finished;
      return {
        ...finished,
        current: null,
        autoSelected: false,
        versions: [],
        selectedVersion: null,
        diffBaseContent: null,
        viewMode: 'preview',
      };
    }),

  // ARTIFACT_CREATED: a new artifact appeared this turn. REST list no longer
  // surfaces unflushed artifacts (overlay removed), so we upsert it into the
  // list FROM the event. Auto-open it unless the user has actively picked
  // another artifact — applies to EVERY source incl. source='tool' (web_fetch
  // blobs, overflow dumps): the user should see live what the system produced,
  // tool outputs included, not have them hidden behind a later agent artifact.
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
        hasBlob: !!d.has_blob,
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
        has_blob: !!d.has_blob,
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
      // Auto-open regardless of source — still yields to an artifact the user
      // actively selected (autoSelected === false), but a tool-persisted one no
      // longer silently lists behind a later agent artifact that grabs `current`.
      if (!s.current || s.autoSelected) {
        next.current = liveToDetail(d.id, live, s.sessionId);
        next.autoSelected = true;
        next.openArtifactIds = s.openArtifactIds.includes(d.id)
          ? s.openArtifactIds
          : [...s.openArtifactIds, d.id];
        next.viewMode = defaultViewMode(d.content_type, d.has_blob);
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
      // Blob overwrite carries has_blob/content_type so a cross-turn artifact
      // with no live base still renders as binary, not an empty markdown view.
      const live: LiveArtifact = {
        content,
        version: d.current_version,
        contentType: d.content_type ?? base?.contentType ?? 'text/markdown',
        title: base?.title ?? d.id,
        source: base?.source ?? 'agent',
        omitted,
        originalFilename: base?.originalFilename ?? null,
        hasBlob: d.has_blob ?? base?.hasBlob ?? false,
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
        next.openArtifactIds = s.openArtifactIds.includes(d.id)
          ? s.openArtifactIds
          : [...s.openArtifactIds, d.id];
        next.viewMode = defaultViewMode(live.contentType, live.hasBlob);
        next.versions = [];
        next.selectedVersion = null;
      }
      return next;
    }),

  selectFromLive: (id) => {
    const live = get().liveContent[id];
    if (!live || live.omitted) return false;
    set((s) => ({
      current: liveToDetail(id, live, get().sessionId),
      autoSelected: false, // user-picked: keep them here at COMPLETE
      openArtifactIds: s.openArtifactIds.includes(id)
        ? s.openArtifactIds
        : [...s.openArtifactIds, id],
      viewMode: defaultViewMode(live.contentType, live.hasBlob),
      versions: [],
      selectedVersion: null,
      diffBaseContent: null,
    }));
    return true;
  },

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
      openArtifactIds: [],
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
