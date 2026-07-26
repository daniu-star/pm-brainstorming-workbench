const USER_TOKEN_KEY = "pm-brainstorm-user-token";
const API_KEY_STORAGE_KEY = "pm-brainstorm-api-key";
const BASE_URL_STORAGE_KEY = "pm-brainstorm-base-url";
const MODEL_STORAGE_KEY = "pm-brainstorm-model";
const JWT_TOKEN_KEY = "pm-brainstorm-jwt-token";

export function getUserToken(): string {
  if (typeof window === "undefined") return "";
  let token = localStorage.getItem(USER_TOKEN_KEY);
  if (!token) {
    token = crypto.randomUUID();
    localStorage.setItem(USER_TOKEN_KEY, token);
  }
  return token;
}

export function getStoredApiKey(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(API_KEY_STORAGE_KEY) || "";
}

export function getStoredBaseUrl(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(BASE_URL_STORAGE_KEY) || "";
}

export function getStoredModel(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(MODEL_STORAGE_KEY) || "";
}

export function saveApiKeyConfig(apiKey: string, baseUrl: string, model: string): void {
  localStorage.setItem(API_KEY_STORAGE_KEY, apiKey);
  localStorage.setItem(BASE_URL_STORAGE_KEY, baseUrl);
  localStorage.setItem(MODEL_STORAGE_KEY, model);
}

export function clearApiKeyConfig(): void {
  localStorage.removeItem(API_KEY_STORAGE_KEY);
  localStorage.removeItem(BASE_URL_STORAGE_KEY);
  localStorage.removeItem(MODEL_STORAGE_KEY);
}

export function hasUserApiKey(): boolean {
  return !!getStoredApiKey();
}

export function saveJwtToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(JWT_TOKEN_KEY, token);
}

export function getJwtToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(JWT_TOKEN_KEY);
}

export function clearJwtToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(JWT_TOKEN_KEY);
}

export function isLoggedIn(): boolean {
  return !!getJwtToken();
}

export function logout(): void {
  clearJwtToken();
}

export function getUserHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const jwtToken = getJwtToken();
  if (jwtToken) {
    headers["Authorization"] = `Bearer ${jwtToken}`;
  } else {
    const token = getUserToken();
    if (token) headers["X-User-Token"] = token;
  }
  const apiKey = getStoredApiKey();
  if (apiKey) {
    headers["X-API-Key"] = apiKey;
    const baseUrl = getStoredBaseUrl();
    const model = getStoredModel();
    if (baseUrl) headers["X-Base-URL"] = baseUrl;
    if (model) headers["X-Model"] = model;
  }
  return headers;
}
