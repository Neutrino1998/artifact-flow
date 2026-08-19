import { create } from 'zustand';
import { useConversationStore } from './conversationStore';
import { useArtifactStore } from './artifactStore';
import { useStreamStore } from './streamStore';
import type { UserInfo } from '@/types';

export type { UserInfo };

export type AuthNotice = 'session_expired' | null;

interface AuthState {
  token: string | null;
  user: UserInfo | null;
  isAuthenticated: boolean;
  isHydrated: boolean;
  notice: AuthNotice;

  login: (token: string, user: UserInfo) => void;
  logout: (notice?: Exclude<AuthNotice, null>) => void;
  clearNotice: () => void;
  hydrate: () => void;
  /** 用最新 UserInfo 覆盖 store + localStorage 缓存 — 用于 hydrate 后追平后端新增字段 */
  setUser: (user: UserInfo) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  isAuthenticated: false,
  isHydrated: false,
  notice: null,

  login: (token, user) => {
    localStorage.setItem('af_token', token);
    localStorage.setItem('af_user', JSON.stringify(user));
    set({ token, user, isAuthenticated: true, notice: null });
  },

  logout: (notice) => {
    localStorage.removeItem('af_token');
    localStorage.removeItem('af_user');
    useConversationStore.getState().reset();
    useArtifactStore.getState().reset();
    useStreamStore.getState().reset();
    set({ token: null, user: null, isAuthenticated: false, notice: notice ?? null });
  },

  clearNotice: () => set({ notice: null }),

  setUser: (user) => {
    localStorage.setItem('af_user', JSON.stringify(user));
    set({ user });
  },

  hydrate: () => {
    const token = localStorage.getItem('af_token');
    const userStr = localStorage.getItem('af_user');
    if (token && userStr) {
      try {
        const user = JSON.parse(userStr) as UserInfo;
        set({ token, user, isAuthenticated: true, isHydrated: true });
        return;
      } catch {
        // fall through
      }
    }
    set({ isHydrated: true });
  },
}));
