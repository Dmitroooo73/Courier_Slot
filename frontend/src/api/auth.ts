export interface Account {
  id: number;
  login: string;
  role: "user" | "admin";
  created_at: string;
  preferred_zone: string | null;
}

export interface ProfileEdit {
  display_name: string;
  bio: string;
  birthday: string | null;
  preferred_zone: string | null;
}

export interface Profile extends Omit<ProfileEdit, "preferred_zone"> {
  account: Account;
  avatar_id: string | null;
  disabled_at: string | null;
  active_sessions: number;
}

export interface DirectoryEntry extends Omit<Account, "preferred_zone"> {
  display_name: string;
  disabled_at: string | null;
}

export interface Session {
  access_token: string;
  user: Account;
}

export interface UserPage {
  users: DirectoryEntry[];
  next_after: number | null;
  stats: { accounts: number; admins: number; disabled: number; active_sessions: number };
}

export class AuthError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export function authMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Запрос не удалось выполнить.";
}

async function check(response: Response): Promise<Response> {
  if (response.ok) return response;
  const result = await response.json().catch(() => ({}));
  const detail = typeof result.detail === "string" ? result.detail : "Запрос не удалось выполнить.";
  throw new AuthError(response.status, detail);
}

async function post(path: string, data?: { login: string; password: string }): Promise<Response> {
  const send = async () => check(await fetch(`/api/auth/${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Pult-Auth": "1", "Content-Type": "application/json" },
    body: data && JSON.stringify(data),
  }));
  // Each refresh consumes the previous cookie, including across tabs.
  return navigator.locks ? navigator.locks.request("pult-auth-cookie", send) : send();
}

let refreshing: Promise<Session | null> | undefined;

export function restoreSession(): Promise<Session | null> {
  refreshing ??= post("refresh").then((response) => response.json()).catch((error: unknown) => {
    if (error instanceof AuthError && error.status === 401) return null;
    throw error;
  }).finally(() => { refreshing = undefined; });
  return refreshing;
}

export async function signIn(mode: "login" | "register", login: string, password: string): Promise<Session> {
  return (await post(mode, { login, password })).json();
}

export async function signOut(): Promise<void> {
  await post("logout");
}

export function apiRequest(session: Session, path: string, options?: RequestInit): Promise<Response> {
  const headers = new Headers(options?.headers);
  headers.set("Authorization", `Bearer ${session.access_token}`);
  headers.set("X-Pult-Auth", "1");
  return fetch(`/api${path}`, { ...options, headers }).then(check);
}

export function jsonRequest(method: string, data: object): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
}
