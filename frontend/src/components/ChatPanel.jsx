import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

const WELCOME = {
  role: "assistant",
  content:
    "Hi! Ask me anything about your shares — I answer from this app's AI research " +
    "and your live watchlist. Try: \"Should I hold Infosys?\" or tap the mic and speak.",
};

export default function ChatPanel() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([WELCOME]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef(null);
  const bottomRef = useRef(null);

  const speechSupported =
    typeof window !== "undefined" &&
    (window.SpeechRecognition || window.webkitSpeechRecognition);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, open]);

  const send = async (text) => {
    const message = (text ?? input).trim();
    if (!message || busy) return;
    setInput("");
    const history = messages
      .filter((m) => m !== WELCOME)
      .map(({ role, content }) => ({ role, content }));
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    setBusy(true);
    try {
      const res = await api.chat(message, history);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.reply, sources: res.sources },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `⚠ ${e.message}`, error: true },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const toggleVoice = () => {
    if (!speechSupported) return;
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new SR();
    rec.lang = "en-IN";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = (e) => {
      const transcript = e.results[0][0].transcript;
      setInput(transcript);
      send(transcript);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    recognitionRef.current = rec;
    setListening(true);
    rec.start();
  };

  if (!open) {
    return (
      <button className="chat-fab" onClick={() => setOpen(true)} title="Ask the AI">
        💬
      </button>
    );
  }

  return (
    <div className="chat-panel">
      <div className="chat-head">
        <span>💬 Research chat</span>
        <button className="close-btn" onClick={() => setOpen(false)}>✕</button>
      </div>
      <div className="chat-messages">
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg ${m.role}${m.error ? " err" : ""}`}>
            <div className="bubble">{m.content}</div>
            {m.sources?.length > 0 && (
              <div className="chat-sources">
                grounded in:{" "}
                {m.sources
                  .map((s) => (s.symbol ? `${s.symbol} (${s.date})` : `top-20 ${s.date}`))
                  .join(", ")}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="chat-msg assistant"><div className="bubble muted">Thinking…</div></div>}
        <div ref={bottomRef} />
      </div>
      <div className="chat-input-row">
        <button
          className={`mic-btn${listening ? " listening" : ""}`}
          onClick={toggleVoice}
          disabled={!speechSupported}
          title={speechSupported ? "Speak your question" : "Voice input needs Chrome"}
        >
          {listening ? "🔴" : "🎙"}
        </button>
        <input
          placeholder={listening ? "Listening…" : "Ask about your shares…"}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          disabled={busy}
        />
        <button onClick={() => send()} disabled={busy || !input.trim()}>Send</button>
      </div>
      <div className="chat-disclaimer">AI answers from app research · not financial advice</div>
    </div>
  );
}
