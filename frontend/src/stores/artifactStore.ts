import { create } from 'zustand';
import type { ArtifactSummary, ArtifactDetail, VersionSummary, VersionDetail } from '@/types';

export type ArtifactViewMode = 'preview' | 'source' | 'diff';

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
  setUploading: (uploading: boolean) => void;
  setUploadError: (error: string | null) => void;
  reset: () => void;
}

function defaultViewMode(contentType?: string): ArtifactViewMode {
  if (contentType === 'text/markdown') return 'preview';
  return 'source';
}

export const useArtifactStore = create<ArtifactState>((set) => ({
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
  setUploading: (uploading) => set({ uploading }),
  setUploadError: (error) => set({ uploadError: error }),
  reset: () =>
    set({
      sessionId: null,
      artifacts: [],
      current: null,
      autoSelected: false,
      versions: [],
      selectedVersion: null,
      diffBaseContent: null,
      viewMode: 'preview',
      pendingUpdates: [],
      uploading: false,
      uploadError: null,
    }),
}));
