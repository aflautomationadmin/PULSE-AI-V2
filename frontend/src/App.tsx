import { useState, useEffect, useRef, useCallback } from 'react';
import { v4 as uuid } from 'uuid';
import { MsalProvider, AuthenticatedTemplate, UnauthenticatedTemplate } from '@azure/msal-react';
import { msalInstance } from './auth/msalConfig';
import { useAuth } from './auth/useAuth';
import LoginPage from './pages/LoginPage';
import { Sidebar } from './components/Sidebar';
import { Message, type MessageItem } from './components/Message';
import { ChatInput } from './components/ChatInput';
import { Toast } from './components/Toast';
import { streamMessage, listThreads, getThreadMessages, setTokenGetter } from './api/client';
import type { Thread } from './api/types';
import './styles/chatbot.css';

// Progress messages shown while bot is thinking
const PROGRESS_NOTES = [
  'Understanding your request…',
  'Checking the data…',
  'Gathering the latest figures…',
  'Running the analysis…',
  'Almost there…',
];

const SAMPLE_QUESTIONS = [
  'Top 5 brands by net sales last month',
  'State-wise sales for USPA this quarter',
  'Compare ARROW vs FM YTD performance',
  'Daily sales trend for Tommy Hilfiger',
];

// ── Inner chat app (only renders when authenticated) ─────────────────────────

function ChatApp() {
  const { email, name, getToken, logout } = useAuth();

  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [progressNote, setProgressNote] = useState('');
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeThread, setActiveThread] = useState('');
  const [toast, setToast] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const progressTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // Wire the token getter into the API client so all requests carry a Bearer token
  useEffect(() => {
    setTokenGetter(getToken);
  }, [getToken]);

  const refreshThreads = useCallback(async (loadMessages = false) => {
    try {
      const data = await listThreads();
      setThreads(data.threads);
      setActiveThread(data.active);
      if (loadMessages && data.active) {
        const msgs = await getThreadMessages(data.active);
        if (msgs && msgs.length > 0) {
          setMessages(rebuildMessages(msgs));
        }
      }
    } catch { /* backend may not be ready */ }
  }, []);

  useEffect(() => { refreshThreads(true); }, [refreshThreads]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  function startProgress() {
    let idx = 0;
    setProgressNote(PROGRESS_NOTES[0]);
    progressTimer.current = setInterval(() => {
      idx = Math.min(idx + 1, PROGRESS_NOTES.length - 1);
      setProgressNote(PROGRESS_NOTES[idx]);
    }, 3500);
  }

  function stopProgress() {
    if (progressTimer.current) clearInterval(progressTimer.current);
    setProgressNote('');
  }

  async function handleSend(text: string) {
    const userMsg: MessageItem = { id: uuid(), role: 'user', text, timestamp: new Date() };
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);
    startProgress();

    const botId = uuid();
    const botTimestamp = new Date();
    setMessages(prev => [...prev, {
      id: botId, role: 'bot', text: '', timestamp: botTimestamp, streaming: true,
    }]);

    try {
      let fullText = '';

      for await (const event of streamMessage(text, activeThread || undefined)) {
        if (event.type === 'start') {
          stopProgress();

        } else if (event.type === 'token') {
          fullText += event.content;
          setMessages(prev => prev.map(m =>
            m.id === botId ? { ...m, text: fullText } : m
          ));

        } else if (event.type === 'complete') {
          fullText = event.content;
          const resp: import('./api/types').ChatResponse = {
            route: 'normal_chat',
            answer_text: fullText,
            sql_used: event.sql_used ?? null,
            sql_explanation: null,
            chart_data: null,
            chart_type: null,
            row_preview: null,
            citations: [],
            verification: null,
            last_sql: event.sql_used ?? null,
            last_entity_match: null,
            last_resolver_explanation: null,
            cache_status: null,
            trace_id: event.trace_id ?? null,
          };
          setMessages(prev => prev.map(m =>
            m.id === botId ? { ...m, text: fullText, streaming: false, response: resp } : m
          ));
          stopProgress();

        } else if (event.type === 'metadata') {
          const resolvedText = fullText || event.answer || '';
          const resp: import('./api/types').ChatResponse = {
            route: 'business_question',
            answer_text: resolvedText,
            sql_used: event.sql_used,
            sql_explanation: event.sql_explanation,
            chart_data: event.chart_data,
            chart_type: event.chart_type,
            row_preview: event.row_preview,
            citations: event.citations,
            verification: event.verification,
            last_sql: event.sql_used,
            last_entity_match: null,
            last_resolver_explanation: event.last_resolver_explanation,
            cache_status: event.cache_status ?? null,
            trace_id: event.trace_id ?? null,
          };
          setMessages(prev => prev.map(m =>
            m.id === botId ? { ...m, text: resolvedText, streaming: false, response: resp } : m
          ));

        } else if (event.type === 'error') {
          fullText = `⚠ ${event.content}`;
          setMessages(prev => prev.map(m =>
            m.id === botId ? { ...m, text: fullText, streaming: false } : m
          ));
          stopProgress();
        }
      }

      setMessages(prev => prev.map(m =>
        m.id === botId ? { ...m, streaming: false } : m
      ));
      refreshThreads();

    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : 'Something went wrong.';
      setMessages(prev => prev.map(m =>
        m.id === botId ? { ...m, text: `⚠ ${detail}`, streaming: false } : m
      ));
      stopProgress();
    } finally {
      setLoading(false);
    }
  }

  function rebuildMessages(rawMessages: Record<string, unknown>[]): MessageItem[] {
    return rawMessages.map((m) => {
      if (m.role === 'user') {
        return { id: uuid(), role: 'user' as const, text: String(m.text ?? ''), timestamp: new Date() };
      }
      const response: import('./api/types').ChatResponse = {
        route: (m.route as 'business_question' | 'normal_chat') ?? 'normal_chat',
        answer_text: String(m.text ?? ''),
        sql_used: (m.sql_used as string) ?? null,
        sql_explanation: (m.sql_explanation as string) ?? null,
        chart_data: (m.chart_data as import('./api/types').ChartData) ?? null,
        chart_type: (m.chart_type as string) ?? null,
        row_preview: (m.row_preview as Record<string, unknown>[]) ?? null,
        citations: (m.citations as import('./api/types').Citation[]) ?? [],
        verification: (m.verification as import('./api/types').VerificationResult) ?? null,
        last_sql: (m.sql_used as string) ?? null,
        last_entity_match: null,
        last_resolver_explanation: (m.last_resolver_explanation as string) ?? null,
        cache_status: null,
        trace_id: null,   // not persisted in memory turns
      };
      return { id: uuid(), role: 'bot' as const, text: String(m.text ?? ''), response, timestamp: new Date() };
    });
  }

  function handleThreadChange(_threadId?: string, rawMessages?: Record<string, unknown>[]) {
    refreshThreads();
    setMessages(rawMessages && rawMessages.length > 0 ? rebuildMessages(rawMessages) : []);
  }

  function handleChipClick(q: string) { handleSend(q); }

  const showWelcome = messages.length === 0 && !loading;

  return (
    <div className="app-shell">
      <Sidebar
        threads={threads}
        activeThread={activeThread}
        onThreadChange={handleThreadChange}
        onNotify={setToast}
      />

      <main className="main-area">
        {/* Header */}
        <header className="chat-header">
          <img src="/LOgo.png" alt="Arvind Fashions" className="chat-header-logo" />
          <div className="chat-header-title">
            Pulse AI
            <span>Retail Analytics Assistant · Arvind Fashions</span>
          </div>
          <div className="chat-header-right">
            {activeThread && (
              <span
                className="thread-badge"
                title="Current thread"
                onClick={() => { navigator.clipboard?.writeText(activeThread); setToast('Thread ID copied!'); }}
              >
                {activeThread}
              </span>
            )}
            {/* User info + sign-out */}
            <div className="user-chip" title={email}>
              <span className="user-avatar">{(name || email).charAt(0).toUpperCase()}</span>
              <span className="user-name">{name || email}</span>
              <button className="signout-btn" onClick={logout} title="Sign out">↩</button>
            </div>
          </div>
        </header>

        {/* Messages */}
        <div className="chatbot-messages">
          {showWelcome && (
            <div className="welcome-card">
              <img src="/digitalization.gif" alt="bot" className="welcome-avatar" />
              <div className="welcome-title">Hello{name ? `, ${name.split(' ')[0]}` : ''}! How can I help you today?</div>
              <div className="welcome-sub">
                Ask me about Arvind Fashions retail sales — brands, stores, regions,
                KPIs, trends and more. I'll fetch the data and explain it in plain English.
              </div>
              <div className="welcome-chips">
                {SAMPLE_QUESTIONS.map(q => (
                  <button key={q} className="welcome-chip" onClick={() => handleChipClick(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map(msg => (
            <Message key={msg.id} message={msg} />
          ))}

          {loading && progressNote && (
            <div className="thinking-row">
              <img src="/digitalization.gif" alt="bot" className="msg-avatar" style={{ background: 'transparent', border: 'none', boxShadow: 'none', width: 34, height: 34 }} />
              <div className="thinking-bubble">
                <div className="typing-dots">
                  <div className="typing-dot" />
                  <div className="typing-dot" />
                  <div className="typing-dot" />
                </div>
                <div className="progress-note">{progressNote}</div>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        <ChatInput onSend={handleSend} disabled={loading} />
      </main>

      <Toast message={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

// ── Root component — wraps everything in MSAL provider ───────────────────────

export default function App() {
  return (
    <MsalProvider instance={msalInstance}>
      <AuthenticatedTemplate>
        <ChatApp />
      </AuthenticatedTemplate>
      <UnauthenticatedTemplate>
        <LoginPage />
      </UnauthenticatedTemplate>
    </MsalProvider>
  );
}
