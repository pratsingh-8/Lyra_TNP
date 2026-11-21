import { useEffect, useRef, useState } from "react";

const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

// A new session starts on every page load.
const newSessionId = () =>
  "s-" + (crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10));

function errorText(status, body) {
  const d = body && body.detail;
  if (typeof d === "string") return d === "all providers unavailable"
    ? "All AI providers are unavailable right now. Please try again in a moment."
    : d;
  if (Array.isArray(d))
    return "Invalid request: " + d.map((e) => e.msg || JSON.stringify(e)).join("; ");
  return `Request failed (HTTP ${status}).`;
}

export default function App() {
  const [sessionId] = useState(newSessionId);
  const [items, setItems] = useState([]); // {kind: user|assistant|error, ...}
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items, loading]);

  async function send() {
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    setItems((p) => [...p, { kind: "user", text }]);
    setLoading(true);
    try {
      const headers = { "Content-Type": "application/json" };
      if (simulate.trim()) headers["X-Simulate-Failure"] = simulate.trim();
      const payload = { session_id: sessionId, message: text };
      if (systemPrompt.trim()) payload.system_prompt = systemPrompt.trim();

      const res = await fetch(`${API_URL}/chat`, { method: "POST", headers, body: JSON.stringify(payload) });
      let body = null;
      try { body = await res.json(); } catch { /* non-JSON body */ }
      if (!res.ok) {
        setItems((p) => [...p, { kind: "error", text: errorText(res.status, body) }]);
      } else {
        setItems((p) => [...p, { kind: "assistant", text: body.reply, meta: body.metadata }]);
      }
    } catch {
      setItems((p) => [...p, { kind: "error", text: "Could not reach the server. Check your connection and try again." }]);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Resilient AI Chat</h1>
        <span className="sid">session {sessionId}</span>
      </header>

      <details className="settings">
        <summary>Session settings</summary>
        <label>
          System prompt
          <textarea
            data-testid="system-prompt-input"
            rows={2}
            value={systemPrompt}
            placeholder="Optional. Overrides the default system prompt for this session."
            onChange={(e) => setSystemPrompt(e.target.value)}
          />
        </label>
        <label>
          Simulate failure (demo)
          <input
            data-testid="simulate-failure-input"
            value={simulate}
            placeholder="e.g. key_1=429  or  *=429"
            onChange={(e) => setSimulate(e.target.value)}
          />
        </label>
      </details>

      <main className="log" aria-live="polite">
        {items.length === 0 && <p className="empty">Send a message to start. Each reply shows where it came from, which model answered, latency and token usage.</p>}
        {items.map((it, i) =>
          it.kind === "user" ? (
            <div key={i} className="msg user" data-testid="user-message">{it.text}</div>
          ) : it.kind === "error" ? (
            <div key={i} className="msg error" role="alert" data-testid="error-message">{it.text}</div>
          ) : (
            <div key={i} className="msg assistant" data-testid="assistant-message">
              <div className="text">{it.text}</div>
              <dl className="meta">
                <div><dt>source</dt><dd data-testid="meta-source" className={it.meta.resolution_source}>{it.meta.resolution_source}</dd></div>
                <div><dt>model</dt><dd data-testid="meta-model">{it.meta.model}</dd></div>
                <div><dt>latency</dt><dd data-testid="meta-latency">{it.meta.latency_ms} ms</dd></div>
                <div><dt>input tokens</dt><dd data-testid="meta-input-tokens">{it.meta.input_tokens}</dd></div>
                <div><dt>output tokens</dt><dd data-testid="meta-output-tokens">{it.meta.output_tokens}</dd></div>
                {it.meta.key_id && (
                  <div><dt>key</dt><dd>{it.meta.key_id}{it.meta.fallback_attempts > 0 ? ` (after ${it.meta.fallback_attempts} failover)` : ""}</dd></div>
                )}
              </dl>
            </div>
          )
        )}
        {loading && <div className="msg pending">Thinking…</div>}
        <div ref={endRef} />
      </main>

      <footer>
        <textarea
          data-testid="chat-input"
          rows={2}
          value={input}
          placeholder="Type a message. Enter to send, Shift+Enter for a new line."
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button data-testid="send-button" onClick={send} disabled={loading}>Send</button>
      </footer>
    </div>
  );
}
