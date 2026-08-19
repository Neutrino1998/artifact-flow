'use client';

import { create } from 'zustand';
import { getClientConfig } from '@/lib/api';
import type { PasswordPolicy } from '@/lib/passwordPolicy';

// Backend-owned runtime constants (GET /api/v1/meta). The frontend reads these
// from the server instead of hardcoding values that would drift from
// src/config.py. Values are static for the session — fetchConfig() runs once
// (guarded by `fetched`) and the result is cached for the app's lifetime.
interface ConfigState {
  compactionThreshold: number | null;
  leadAgentModel: string | null;
  // Per-file upload byte limit (backend MAX_UPLOAD_SIZE). null until fetched —
  // the composer's size pre-gate skips when null (best-effort; backend 422s
  // anyway), so we never block a file on a not-yet-loaded limit.
  maxUploadSize: number | null;
  // Per-user private skill count: -1 unlimited, 0 personal imports disabled,
  // positive = limit. null until /meta loads; backend remains authoritative.
  maxPrivateSkills: number | null;
  // Optional message-feedback detail limit. null means the client pre-gate is
  // unavailable; the backend remains authoritative.
  messageFeedbackMaxDetailChars: number | null;
  // Password-shape policy from the backend. null until /meta loads; password
  // forms then show generic guidance and never reject locally.
  passwordPolicy: PasswordPolicy | null;
  fetched: boolean;
  fetchConfig: () => Promise<void>;
}

export const useConfigStore = create<ConfigState>((set, get) => ({
  compactionThreshold: null,
  leadAgentModel: null,
  maxUploadSize: null,
  maxPrivateSkills: null,
  messageFeedbackMaxDetailChars: null,
  passwordPolicy: null,
  fetched: false,
  fetchConfig: async () => {
    if (get().fetched) return;
    try {
      const cfg = await getClientConfig();
      set({
        compactionThreshold: cfg.compaction_token_threshold,
        leadAgentModel: cfg.lead_agent_model,
        maxUploadSize: cfg.max_upload_size,
        maxPrivateSkills: cfg.max_private_skills,
        messageFeedbackMaxDetailChars: cfg.message_feedback_max_detail_chars,
        passwordPolicy: {
          minLength: cfg.password_policy.min_length,
          maxBytes: cfg.password_policy.max_bytes,
          requireLetter: cfg.password_policy.require_letter,
          requireDigit: cfg.password_policy.require_digit,
          requireSymbol: cfg.password_policy.require_symbol,
        },
        fetched: true,
      });
    } catch (err) {
      // Best-effort: the context-usage gauge / model label simply render
      // without their values (or hide) if this fails. Don't block the UI on it.
      console.error('Failed to fetch client config:', err);
    }
  },
}));
