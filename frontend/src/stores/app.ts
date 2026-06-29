import { defineStore } from "pinia";
import { clearStoredAuthToken, getStoredAuthToken, setStoredAuthToken } from "../services/api";

const CURRENT_USER_STORAGE_KEY = "print_nesting_current_user";

export interface CurrentUser {
  user_id: string;
  email: string;
  display_name: string;
  roles: string[];
  permissions: string[];
}

function getStoredCurrentUser(): CurrentUser | null {
  if (typeof window === "undefined") {
    return null;
  }
  const raw = window.localStorage.getItem(CURRENT_USER_STORAGE_KEY);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw) as CurrentUser;
  } catch {
    window.localStorage.removeItem(CURRENT_USER_STORAGE_KEY);
    return null;
  }
}

function setStoredCurrentUser(user: CurrentUser | null): void {
  if (typeof window === "undefined") {
    return;
  }
  if (user) {
    window.localStorage.setItem(CURRENT_USER_STORAGE_KEY, JSON.stringify(user));
  } else {
    window.localStorage.removeItem(CURRENT_USER_STORAGE_KEY);
  }
}

export function userHasPermission(user: CurrentUser | null, permission: string): boolean {
  const permissions = user?.permissions || [];
  return permissions.includes("*") || permissions.includes(permission);
}

export function userHasAnyPermission(user: CurrentUser | null, permissions: string[]): boolean {
  return permissions.length === 0 || permissions.some((permission) => userHasPermission(user, permission));
}

export const useAppStore = defineStore("app", {
  state: () => ({
    lastSolutionId: "",
    lastJobId: "",
    apiHealthy: false,
    authToken: getStoredAuthToken(),
    currentUser: getStoredCurrentUser() as CurrentUser | null
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.authToken && state.currentUser),
    hasPermission: (state) => (permission: string) => userHasPermission(state.currentUser, permission),
    hasAnyPermission: (state) => (permissions: string[]) => userHasAnyPermission(state.currentUser, permissions)
  },
  actions: {
    setLastSolution(solutionId: string, jobId: string) {
      this.lastSolutionId = solutionId;
      this.lastJobId = jobId;
    },
    setToken(token: string) {
      this.authToken = token;
      setStoredAuthToken(token);
    },
    setCurrentUser(user: CurrentUser) {
      this.currentUser = user;
      setStoredCurrentUser(user);
    },
    setAuth(token: string, user: CurrentUser) {
      this.setToken(token);
      this.setCurrentUser(user);
    },
    clearAuth() {
      this.authToken = "";
      this.currentUser = null;
      clearStoredAuthToken();
      setStoredCurrentUser(null);
    }
  }
});
