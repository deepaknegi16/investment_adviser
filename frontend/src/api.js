const TOKEN_KEY = "adviser_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { headers, ...options });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    setToken(null);
    window.dispatchEvent(new Event("auth-expired"));
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  login: (username, password) =>
    request("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  watchlist: () => request("/api/watchlist"),
  addShare: (symbol, name) =>
    request("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol, name }) }),
  removeShare: (symbol) => request(`/api/watchlist/${symbol}`, { method: "DELETE" }),
  search: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),
  history: (symbol, period) => request(`/api/stocks/${symbol}/history?period=${period}`),
  analysis: (symbol, refresh = false) =>
    request(`/api/stocks/${symbol}/analysis${refresh ? "?refresh=true" : ""}`),
  picks: (refresh = false) => request(`/api/picks${refresh ? "?refresh=true" : ""}`),
  chat: (message, history) =>
    request("/api/chat", { method: "POST", body: JSON.stringify({ message, history }) }),
};
