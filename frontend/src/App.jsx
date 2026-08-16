import { useCallback, useEffect, useState } from "react";
import { api, getToken, setToken } from "./api.js";
import PortfolioTable from "./components/PortfolioTable.jsx";
import PicksTable from "./components/PicksTable.jsx";
import StockDrawer from "./components/StockDrawer.jsx";
import AddShareDialog from "./components/AddShareDialog.jsx";
import Login from "./components/Login.jsx";
import ChatPanel from "./components/ChatPanel.jsx";

export default function App() {
  const [authed, setAuthed] = useState(() => Boolean(getToken()));
  const [shares, setShares] = useState(null);
  const [error, setError] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [selected, setSelected] = useState(null);
  const [showAdd, setShowAdd] = useState(false);

  useEffect(() => {
    const onExpired = () => setAuthed(false);
    window.addEventListener("auth-expired", onExpired);
    return () => window.removeEventListener("auth-expired", onExpired);
  }, []);

  const loadWatchlist = useCallback(async () => {
    try {
      const data = await api.watchlist();
      setShares(data.shares);
      setUpdatedAt(new Date());
      setError(null);
    } catch (e) {
      setError(`Could not load market data: ${e.message}`);
    }
  }, []);

  useEffect(() => {
    if (!authed) return;
    loadWatchlist();
    const id = setInterval(loadWatchlist, 60_000);
    return () => clearInterval(id);
  }, [loadWatchlist, authed]);

  const handleAdd = async (symbol, name) => {
    await api.addShare(symbol, name);
    setShowAdd(false);
    loadWatchlist();
  };

  const handleRemove = async (symbol) => {
    await api.removeShare(symbol);
    setSelected(null);
    loadWatchlist();
  };

  if (!authed) {
    return <Login onLogin={() => setAuthed(true)} />;
  }

  const logout = () => {
    setToken(null);
    setShares(null);
    setAuthed(false);
  };

  return (
    <div className="app">
      <div className="topbar">
        <h1>📈 My Shares — Indian Market</h1>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {updatedAt && (
            <span className="meta">
              Last updated {updatedAt.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <button onClick={() => setShowAdd(true)}>＋ Add share</button>
          <button className="ghost" onClick={logout}>Log out</button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="section">
        <div className="section-head">
          <h2>My Portfolio</h2>
        </div>
        <div className="card">
          {shares === null ? (
            <div className="loading">Loading market data…</div>
          ) : (
            <PortfolioTable shares={shares} onSelect={setSelected} />
          )}
        </div>
      </div>

      <PicksTable />

      <p className="disclaimer">
        ⚠ Market data via Yahoo Finance (~15 min delayed). Recommendations and predictions are
        AI/rule generated for information only — not financial advice.
      </p>

      {selected && (
        <StockDrawer
          share={selected}
          onClose={() => setSelected(null)}
          onRemove={handleRemove}
        />
      )}
      {showAdd && <AddShareDialog onAdd={handleAdd} onClose={() => setShowAdd(false)} />}
      <ChatPanel />
    </div>
  );
}
