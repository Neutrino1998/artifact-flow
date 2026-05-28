'use client';

import { create } from 'zustand';
import { getClientConfig } from '@/lib/api';

// Backend-owned runtime constants (GET /api/v1/meta). The frontend reads these
// from the server instead of hardcoding values that would drift from
// src/config.py. Values are static for the session — fetchConfig() runs once
// (guarded by `fetched`) and the result is cached for the app's lifetime.
interface ConfigState {
  compactionThreshold: number | null;
  leadAgentModel: string | null;
  fetched: boolean;
  fetchConfig: () => Promise<void>;
}

export const useConfigStore = create<ConfigState>((set, get) => ({
  compactionThreshold: null,
  leadAgentModel: null,
  fetched: false,
  fetchConfig: async () => {
    if (get().fetched) return;
    try {
      const cfg = await getClientConfig();
      set({
        compactionThreshold: cfg.compaction_token_threshold,
        leadAgentModel: cfg.lead_agent_model,
        fetched: true,
      });
    } catch (err) {
      // Best-effort: the context-usage gauge / model label simply render
      // without their values (or hide) if this fails. Don't block the UI on it.
      console.error('Failed to fetch client config:', err);
    }
  },
}));
