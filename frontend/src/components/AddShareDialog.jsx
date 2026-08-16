import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

export default function AddShareDialog({ onAdd, onClose }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  useEffect(() => {
    clearTimeout(timer.current);
    if (query.trim().length < 2) {
      setResults([]);
      return;
    }
    timer.current = setTimeout(async () => {
      try {
        const data = await api.search(query);
        setResults(data.results);
      } catch {
        setResults([]);
      }
    }, 350);
    return () => clearTimeout(timer.current);
  }, [query]);

  const add = async (symbol, name) => {
    setBusy(true);
    setError(null);
    try {
      await onAdd(symbol, name);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3>Add a share (NSE)</h3>
        <input
          autoFocus
          placeholder="Search by name, e.g. Tata Motors"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {error && <div className="error-banner" style={{ marginTop: 10 }}>{error}</div>}
        <div className="search-results">
          {results.map((r) => (
            <div className="result" key={r.symbol}>
              <span>
                <b>{r.name}</b>{" "}
                <span className="share-symbol">{r.symbol}</span>
              </span>
              <button disabled={busy} onClick={() => add(r.symbol, r.name)}>Add</button>
            </div>
          ))}
          {query.trim().length >= 2 && results.length === 0 && (
            <div className="loading">No NSE matches.</div>
          )}
        </div>
      </div>
    </div>
  );
}
