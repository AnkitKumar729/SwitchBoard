import { useEffect, useRef, useState } from "react";
import { getHealth, sendChat } from "./api.js";
import { AssistantMessage, ErrorMessage, UserMessage } from "./Message.jsx";

// Values for the X-Simulate-Failure header, the backend's failover test hook.
const SIMULATIONS = [
  { value: "", label: "Off: keys behave normally" },
  { value: "key_1=429", label: "key_1 returns 429 (rate limit)" },
  { value: "key_1=500", label: "key_1 returns 500 (server error)" },
  { value: "*=429", label: "Every key returns 429" },
];

export default function App() {
  // The session id lives only in this state, so every page load starts a new session.
  const [sessionId] = useState(() => `s-${crypto.randomUUID()}`);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulateFailure, setSimulateFailure] = useState("");
  const [pending, setPending] = useState(false);
  const [health, setHealth] = useState(null);
  const [panelOpen, setPanelOpen] = useState(false); // small screens only: the settings drawer
  const inputRef = useRef(null);
  const endRef = useRef(null);

  // Ask /health until it answers. A free host that went to sleep needs a moment to wake up.
  useEffect(() => {
    let timer;
    let stopped = false;
    const check = () =>
      getHealth()
        .then((data) => !stopped && setHealth(data))
        .catch(() => {
          if (!stopped) timer = setTimeout(check, 4000);
        });
    check();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);

  async function send() {
    if (pending) return;
    const text = draft.trim();
    setDraft("");
    inputRef.current?.focus();
    setPending(true);
    if (text) setMessages((list) => [...list, { role: "user", content: text }]);
    try {
      // An empty message is sent too: the backend rejects it with 422 and the reason shows in the chat.
      const data = await sendChat({ sessionId, message: text, systemPrompt, simulateFailure });
      setMessages((list) => [...list, { role: "assistant", content: data.reply, metadata: data.metadata }]);
    } catch (error) {
      setMessages((list) => [...list, { role: "error", status: error.status, content: error.message }]);
    } finally {
      setPending(false);
    }
  }

  function onKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send();
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Switchboard, start a new session">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name">
            switch<span>board</span>
          </span>
        </a>
        <div className="topbar-end">
          <p className="status" data-testid="health-status">
            <span className={health ? "status-dot online" : "status-dot"} aria-hidden="true" />
            {health ? (
              <>
                {health.keys_configured} keys
                <span className="status-extra"> · cache {health.cache_enabled ? "on" : "off"}</span>
              </>
            ) : (
              "Connecting to the backend…"
            )}
          </p>
          <button
            type="button"
            className="panel-toggle"
            aria-expanded={panelOpen}
            aria-controls="settings-panel"
            onClick={() => setPanelOpen((open) => !open)}
          >
            Settings
          </button>
        </div>
      </header>

      <div className="layout">
        <main className="chat">
          <div className="messages" aria-live="polite">
            {messages.length === 0 && (
              <section className="welcome">
                <p className="eyebrow">Key failover · Response cache</p>
                <h1>
                  Ask anything. <span>See how every reply was served.</span>
                </h1>
                <p>
                  Each answer shows whether it came from a live model call or the cache, which model and API key
                  answered, how long it took and how many tokens that one request used.
                </p>
              </section>
            )}
            {messages.map((message, index) =>
              message.role === "user" ? (
                <UserMessage key={index} content={message.content} />
              ) : message.role === "assistant" ? (
                <AssistantMessage key={index} content={message.content} metadata={message.metadata} />
              ) : (
                <ErrorMessage key={index} status={message.status} content={message.content} />
              ),
            )}
            {pending && (
              <div className="message assistant thinking">
                <span className="dots" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                Waiting for the model…
              </div>
            )}
            <div ref={endRef} />
          </div>

          <form
            className="composer"
            onSubmit={(event) => {
              event.preventDefault();
              send();
            }}
          >
            <label className="sr-only" htmlFor="chat-input">
              Message
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              className="input"
              data-testid="chat-input"
              rows={1}
              autoFocus
              placeholder="Type a message…"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
            />
            <button className="btn btn-primary" type="submit" data-testid="send-button" disabled={pending}>
              {pending ? "Sending…" : "Send"}
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                <path
                  d="M4 12h15m-6-6 6 6-6 6"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
            <p className="composer-hint">Enter to send · Shift + Enter for a new line</p>
          </form>
        </main>

        <aside id="settings-panel" className={panelOpen ? "panel open" : "panel"} aria-label="Session settings">
          <section className="panel-block">
            <p className="eyebrow">Session</p>
            <p className="session-id" data-testid="session-id">
              {sessionId}
            </p>
            <p className="field-hint">A new session starts on every page load. The backend keeps its history.</p>
          </section>

          <section className="panel-block">
            <label className="field-label" htmlFor="system-prompt">
              System prompt
            </label>
            <textarea
              id="system-prompt"
              className="input"
              data-testid="system-prompt-input"
              rows={4}
              placeholder="Optional. Leave empty to use the default prompt."
              value={systemPrompt}
              onChange={(event) => setSystemPrompt(event.target.value)}
            />
            <p className="field-hint">Used from your next message, for this session only.</p>
          </section>

          <section className="panel-block">
            <label className="field-label" htmlFor="simulate-failure">
              Simulate a key failure
            </label>
            <div className="select-wrap">
              <select
                id="simulate-failure"
                className="input"
                data-testid="simulate-failure-select"
                value={simulateFailure}
                onChange={(event) => setSimulateFailure(event.target.value)}
              >
                {SIMULATIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <p className="field-hint">
              Sends the <code>X-Simulate-Failure</code> header with each message until you switch it off.
            </p>
          </section>

          <section className="legend">
            <p className="eyebrow">Reading a reply</p>
            <dl>
              <div>
                <dt>LIVE_INFERENCE</dt>
                <dd>A key called the model for this request.</dd>
              </div>
              <div>
                <dt>LLM_CACHE</dt>
                <dd>The same model and message list was answered before, so no key was used.</dd>
              </div>
              <div>
                <dt>Failed keys</dt>
                <dd>Keys that failed in this request before another key answered.</dd>
              </div>
            </dl>
          </section>
        </aside>
      </div>
    </div>
  );
}
