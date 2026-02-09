import { create } from 'zustand';
import type { ArtifactSummary, ArtifactDetail, VersionSummary, VersionDetail } from '@/types';

export type ArtifactViewMode = 'preview' | 'source' | 'diff';

interface ArtifactState {
  // List
  artifacts: ArtifactSummary[];
  artifactsLoading: boolean;

  // Current artifact
  current: ArtifactDetail | null;
  currentLoading: boolean;

  // Versions
  versions: VersionSummary[];
  selectedVersion: VersionDetail | null;

  // View
  viewMode: ArtifactViewMode;

  // Pending updates from streaming
  pendingUpdates: string[];

  // Actions
  setArtifacts: (artifacts: ArtifactSummary[]) => void;
  setArtifactsLoading: (loading: boolean) => void;
  setCurrent: (artifact: ArtifactDetail | null) => void;
  setCurrentLoading: (loading: boolean) => void;
  setVersions: (versions: VersionSummary[]) => void;
  setSelectedVersion: (version: VersionDetail | null) => void;
  setViewMode: (mode: ArtifactViewMode) => void;
  addPendingUpdate: (identifier: string) => void;
  clearPendingUpdates: () => void;
  reset: () => void;
}

export const useArtifactStore = create<ArtifactState>((set) => ({
  artifacts: [],
  artifactsLoading: false,

  current: null,
  currentLoading: false,

  versions: [],
  selectedVersion: null,

  viewMode: 'preview',

  pendingUpdates: [],

  setArtifacts: (artifacts) => set({ artifacts }),
  setArtifactsLoading: (loading) => set({ artifactsLoading: loading }),
  setCurrent: (artifact) => set({ current: artifact }),
  setCurrentLoading: (loading) => set({ currentLoading: loading }),
  setVersions: (versions) => set({ versions }),
  setSelectedVersion: (version) => set({ selectedVersion: version }),
  setViewMode: (mode) => set({ viewMode: mode }),
  addPendingUpdate: (identifier) =>
    set((s) => ({
      pendingUpdates: s.pendingUpdates.includes(identifier)
        ? s.pendingUpdates
        : [...s.pendingUpdates, identifier],
    })),
  clearPendingUpdates: () => set({ pendingUpdates: [] }),
  reset: () =>
    set({
      artifacts: [],
      current: null,
      versions: [],
      selectedVersion: null,
      viewMode: 'preview',
      pendingUpdates: [],
    }),
}));
