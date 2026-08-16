async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
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
  watchlist: () => request("/api/watchlist"),
  addShare: (symbol, name) =>
    request("/api/watchlist", { method: "POST", body: JSON.stringify({ symbol, name }) }),
  removeShare: (symbol) => request(`/api/watchlist/${symbol}`, { method: "DELETE" }),
  search: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),
  history: (symbol, period) => request(`/api/stocks/${symbol}/history?period=${period}`),
  analysis: (symbol, refresh = false) =>
    request(`/api/stocks/${symbol}/analysis${refresh ? "?refresh=true" : ""}`),
  picks: (refresh = false) => request(`/api/picks${refresh ? "?refresh=true" : ""}`),
};
