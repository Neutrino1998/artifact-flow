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
      viewMode: artifact ? defaultViewMode(artifact.content_type) : 'preview',
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
  clearPendingUpdates: () => set({ pendingUpdates: [] }),
  setUploading: (uploading) => set({ uploading }),
  setUploadError: (error) => set({ uploadError: error }),
  reset: () =>
    set({
      sessionId: null,
      artifacts: [],
      current: null,
      versions: [],
      selectedVersion: null,
      diffBaseContent: null,
      viewMode: 'preview',
      pendingUpdates: [],
      uploading: false,
      uploadError: null,
    }),
}));
