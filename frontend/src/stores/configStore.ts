'use client';

import { create } from 'zustand';
import { getClientConfig } from '@/lib/api';

// Backend-owned runtime constants (GET /api/v1/meta). The frontend reads these
// from the server instead of hardcoding values that would drift from
// src/config.py. Values are static for the session — fetchConfig() runs once
// (guarded by `fetched`) and the result is cached for the app's lifetime.
interface ConfigState {
  compactionThreshold: number | null;
  fetched: boolean;
  fetchConfig: () => Promise<void>;
}

export const useConfigStore = create<ConfigState>((set, get) => ({
  compactionThreshold: null,
  fetched: false,
  fetchConfig: async () => {
    if (get().fetched) return;
    try {
      const cfg = await getClientConfig();
      set({ compactionThreshold: cfg.compaction_token_threshold, fetched: true });
    } catch (err) {
      // Best-effort: the context-usage gauge simply renders without a
      // denominator (or hides) if this fails. Don't block the UI on it.
      console.error('Failed to fetch client config:', err);
    }
  },
}));
