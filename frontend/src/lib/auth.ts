import { create } from 'zustand';
import { apiFetch } from './api';

export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  role: string;
}

export interface UserProfile {
  id: string;
  email: string;
  full_name: string;
  is_superadmin: boolean;
  workspaces: WorkspaceSummary[];
}

interface AuthState {
  accessToken: string | null;
  user: UserProfile | null;
  activeWorkspaceId: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  setActiveWorkspace: (workspaceId: string) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: null,
  user: null,
  activeWorkspaceId: null,

  login: async (email, password) => {
    const tokens = await apiFetch<{ access_token: string; refresh_token: string }>(
      '/auth/login',
      {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      },
    );
    const profile = await apiFetch<UserProfile>(
      '/auth/me',
      { method: 'GET' },
      tokens.access_token,
    );
    set({
      accessToken: tokens.access_token,
      user: profile,
      activeWorkspaceId: profile.workspaces[0]?.id ?? null,
    });
  },

  logout: () => set({ accessToken: null, user: null, activeWorkspaceId: null }),
  setActiveWorkspace: (workspaceId) => set({ activeWorkspaceId: workspaceId }),
}));
