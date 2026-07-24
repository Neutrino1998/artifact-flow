import { create } from 'zustand';
import type { SiteNotification } from '@/types';

export type NotificationPreviewMode = 'edit' | 'preview';

function newNotification(existing: SiteNotification[]): SiteNotification {
  const base = `notice-${new Date().toISOString().slice(0, 10)}`;
  const ids = new Set(existing.map((n) => n.id));
  let id = base;
  let i = 1;
  while (ids.has(id)) {
    i += 1;
    id = `${base}-${i}`;
  }
  return {
    id,
    severity: 'info',
    title: '',
    body: '',
    starts_at: null,
    ends_at: null,
    dismissible: true,
  };
}

interface NotificationConfigState {
  items: SiteNotification[];
  revision: number | null;
  selectedIndex: number | null;
  loading: boolean;
  saving: boolean;
  dirty: boolean;
  message: string | null;
  error: string | null;
  previewMode: NotificationPreviewMode;
  confirmDelete: boolean;
  setLoaded: (items: SiteNotification[], revision: number | null) => void;
  setLoading: (loading: boolean) => void;
  setSaving: (saving: boolean) => void;
  setError: (error: string | null) => void;
  setMessage: (message: string | null) => void;
  setSelectedIndex: (index: number | null) => void;
  setPreviewMode: (mode: NotificationPreviewMode) => void;
  setConfirmDelete: (confirmDelete: boolean) => void;
  addNotification: () => void;
  updateSelected: (patch: Partial<SiteNotification>) => void;
  deleteSelected: () => void;
}

export const useNotificationConfigStore = create<NotificationConfigState>((set) => ({
  items: [],
  revision: null,
  selectedIndex: null,
  loading: true,
  saving: false,
  dirty: false,
  message: null,
  error: null,
  previewMode: 'edit',
  confirmDelete: false,

  setLoaded: (items, revision) => set((s) => ({
    items,
    revision,
    selectedIndex:
      s.selectedIndex !== null && s.selectedIndex < items.length
        ? s.selectedIndex
        : items.length > 0 ? 0 : null,
    dirty: false,
    message: null,
    error: null,
  })),
  setLoading: (loading) => set({ loading }),
  setSaving: (saving) => set({ saving }),
  setError: (error) => set({ error }),
  setMessage: (message) => set({ message }),
  setSelectedIndex: (selectedIndex) => set({ selectedIndex }),
  setPreviewMode: (previewMode) => set({ previewMode }),
  setConfirmDelete: (confirmDelete) => set({ confirmDelete }),
  addNotification: () => set((s) => {
    const next = newNotification(s.items);
    return {
      items: [...s.items, next],
      selectedIndex: s.items.length,
      dirty: true,
      message: null,
      error: null,
      previewMode: 'edit',
    };
  }),
  updateSelected: (patch) => set((s) => {
    if (s.selectedIndex === null) return {};
    return {
      items: s.items.map((n, index) => (
        index === s.selectedIndex ? { ...n, ...patch } : n
      )),
      dirty: true,
      message: null,
      error: null,
    };
  }),
  deleteSelected: () => set((s) => {
    if (s.selectedIndex === null) return {};
    const next = s.items.filter((_, index) => index !== s.selectedIndex);
    return {
      items: next,
      selectedIndex: next.length === 0 ? null : Math.min(s.selectedIndex, next.length - 1),
      dirty: true,
      message: null,
      error: null,
      confirmDelete: false,
    };
  }),
}));
