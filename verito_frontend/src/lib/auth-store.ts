/**
 * lib/auth-store.ts
 * -----------------
 * Manages the VERITO JWT token in localStorage.
 * Import from here for all auth state — never read localStorage directly.
 */

const TOKEN_KEY = "verito.token.v1";
const USER_KEY = "verito.user.v1";

export interface StoredUser {
  user_id: string;
  name: string;
  email: string;
}

// ── Token ──────────────────────────────────────────────────────────────────────

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function isAuthenticated(): boolean {
  return Boolean(getToken());
}

// ── User ───────────────────────────────────────────────────────────────────────

export function getUser(): StoredUser | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StoredUser;
  } catch {
    return null;
  }
}

export function setUser(user: StoredUser): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}
