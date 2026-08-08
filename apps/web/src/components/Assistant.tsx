import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useAuth } from "../auth";
import { ASSISTANT_NAME } from "../brand";

/** A plain line-art mic glyph, standing in for the emoji font everywhere the
 * assistant needs a mic: it renders identically across platforms (the emoji
 * mic glyph looks noticeably different on Windows/macOS/Linux) and reads as
 * part of the same "console" language as the orb rather than a system emoji
 * dropped on top of it. */
function MicGlyph({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="9" y="2.5" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.6" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0" stroke="currentColor" strokeWidth="1.6"
            strokeLinecap="round" />
      <path d="M12 18v3.2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M8.5 21.2h7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

interface ChatProvenance {
  source: string;
  producer: string;
  tier?: string | null;
  model_id?: string | null;
  latency_ms?: number | null;
  degradations: string[];
  prompt_version?: string | null;
}

interface ChatMessage {
  id: string;
  role: "user" | "bot";
  text: string;
  provenance?: ChatProvenance;
  groundedOn?: string[];
  error?: boolean;
}

/** Minimal shape of the Web Speech API we actually use — not in lib.dom.d.ts. */
interface SpeechRecognitionLike extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: any) => void) | null; // eslint-disable-line @typescript-eslint/no-explicit-any
  onerror: ((event: any) => void) | null; // eslint-disable-line @typescript-eslint/no-explicit-any
  onend: (() => void) | null;
}

function speechRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

let seq = 0;
const nextId = () => `msg-${Date.now()}-${seq++}`;

/**
 * The floating voice + chat assistant.
 *
 * Mounted once at the `Shell` level in App.tsx — sibling to `<Nav>`, present
 * on every authenticated page — so it is always one click away without being
 * a per-page concern.
 *
 * Strictly read-only: it calls exactly one endpoint,
 * `POST /api/v1/assistant/chat`, which explains and narrates from inspection
 * records already on the server. It has no button and no code path that
 * submits an inspection, changes a disposition, or halts the line — those
 * stay on their own pages with their own permission checks.
 */
export function Assistant() {
  const { authFetch, user } = useAuth();
  // On /workbench/:id this picks up the unit the user is currently looking
  // at, so a question like "what's wrong with this one" has something to
  // ground on without any extra plumbing between the router and this
  // component.
  const params = useParams<{ id?: string }>();

  const [open, setOpen] = useState(false);
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [voiceSupported, setVoiceSupported] = useState(false);

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const spokenReplyRef = useRef(false); // true only when this turn started as voice input
  const threadRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setVoiceSupported(speechRecognitionCtor() !== null);
  }, []);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [messages, open]);

  useEffect(() => {
    // Stop listening and cancel any speech in flight if the panel closes —
    // an assistant that keeps the mic hot behind a closed panel is exactly
    // the kind of thing an operator would (rightly) not trust.
    if (!open) {
      recognitionRef.current?.stop();
      window.speechSynthesis?.cancel();
    }
  }, [open]);

  if (!user) return null;

  const toggleListening = () => {
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    const Ctor = speechRecognitionCtor();
    if (!Ctor) return;

    const recognition = new Ctor();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event) => {
      let transcript = "";
      for (let i = 0; i < event.results.length; i += 1) {
        transcript += event.results[i][0].transcript;
      }
      setInput(transcript);
    };
    recognition.onerror = () => setListening(false);
    recognition.onend = () => setListening(false);

    recognitionRef.current = recognition;
    spokenReplyRef.current = true;
    setListening(true);
    recognition.start();
  };

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;

    const spoken = spokenReplyRef.current;
    spokenReplyRef.current = false;
    setInput("");
    setMessages((m) => [...m, { id: nextId(), role: "user", text: trimmed }]);
    setBusy(true);

    try {
      const res = await authFetch("/api/v1/assistant/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          correlation_id: params.id ?? null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `assistant unavailable (${res.status})`);
      }
      const data: { reply: string; provenance: ChatProvenance; grounded_on: string[] } =
        await res.json();
      setMessages((m) => [
        ...m,
        { id: nextId(), role: "bot", text: data.reply, provenance: data.provenance,
          groundedOn: data.grounded_on },
      ]);
      if (spoken && window.speechSynthesis) {
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(data.reply));
      }
    } catch (err) {
      setMessages((m) => [
        ...m,
        {
          id: nextId(), role: "bot", error: true,
          text: err instanceof Error ? err.message : "Couldn't reach the assistant.",
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  return (
    <>
      <button
        type="button"
        className="assistant-fab"
        aria-label={open ? `Close ${ASSISTANT_NAME}` : `Open ${ASSISTANT_NAME}, the assistant`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="assistant-fab-icon">
          {open ? "✕" : <MicGlyph size={19} />}
        </span>
      </button>

      {open && (
        <div className="assistant-panel" role="dialog" aria-label={ASSISTANT_NAME}>
          <div className="assistant-head">
            <div>
              <div className="assistant-title">{ASSISTANT_NAME}</div>
              <div className="assistant-sub">
                Read-only · explains inspections, never submits or overrides one
              </div>
            </div>
            <button type="button" onClick={() => setOpen(false)} aria-label="Close">✕</button>
          </div>

          <div className="assistant-thread" ref={threadRef}>
            {messages.length === 0 && (
              <div className="faint" style={{ padding: "6px 2px", lineHeight: 1.6 }}>
                Ask about a recent unit, a verdict, or what a disposition means.
                {user.role === "SHOP_FLOOR_WORKER"
                  ? " I can see your station's recent units."
                  : " I can see the recent inspection feed."}
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`assistant-bubble ${m.role}${m.error ? " error" : ""}`}>
                <div>{m.text}</div>
                {m.provenance && (
                  <div className="assistant-provenance mono">
                    {m.provenance.source.toUpperCase()}
                    {m.provenance.model_id ? ` · ${m.provenance.model_id}` : ""}
                    {typeof m.provenance.latency_ms === "number" ? ` · ${m.provenance.latency_ms}ms` : ""}
                    {m.provenance.degradations.length > 0 ? " · degraded" : ""}
                    {" · SYNTHETIC"}
                  </div>
                )}
              </div>
            ))}
            {busy && <div className="assistant-bubble bot faint">Thinking…</div>}
          </div>

          <form className="assistant-input-row" onSubmit={submit}>
            <button
              type="button"
              className={`assistant-mic${listening ? " listening" : ""}`}
              onClick={toggleListening}
              disabled={!voiceSupported}
              title={voiceSupported ? "Voice input" : "Voice input not supported in this browser"}
              aria-pressed={listening}
              aria-label="Toggle voice input"
            >
              <span className="assistant-mic-icon">
                {listening ? <span className="assistant-mic-dot" /> : <MicGlyph size={16} />}
              </span>
            </button>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={listening ? "Listening…" : "Ask about an inspection…"}
              aria-label="Message"
            />
            <button type="submit" className="molten" disabled={busy || !input.trim()}>
              Send
            </button>
          </form>
        </div>
      )}
    </>
  );
}
