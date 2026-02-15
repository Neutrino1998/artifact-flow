import { create } from 'zustand';

export interface UserInfo {
  id: string;
  username: string;
  display_name: string | null;
  role: string;
}

interface AuthState {
  token: string | null;
  user: UserInfo | null;
  isAuthenticated: boolean;
  isHydrated: boolean;

  login: (token: string, user: UserInfo) => void;
  logout: () => void;
  hydrate: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  isAuthenticated: false,
  isHydrated: false,

  login: (token, user) => {
    localStorage.setItem('af_token', token);
    localStorage.setItem('af_user', JSON.stringify(user));
    set({ token, user, isAuthenticated: true });
  },

  logout: () => {
    localStorage.removeItem('af_token');
    localStorage.removeItem('af_user');
    set({ token: null, user: null, isAuthenticated: false });
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
