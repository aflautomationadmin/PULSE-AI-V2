import axios from 'axios';
import type { ChatResponse, ThreadsResponse, SqlCacheEntry } from './types';

const api = axios.create({ baseURL: '' });

// ── Auth token injection ───────────────────────────────────────────────────────
// Call setTokenGetter(getToken) once on login; both axios and SSE fetch will
// use it to attach Authorization: Bearer <id_token> on every request.
let _getToken: (() => Promise<string>) | null = null;

export function setTokenGetter(fn: () => Promise<string>): void {
  _getToken = fn;
}

api.interceptors.request.use(async (config) => {
  if (_getToken) {
    try {
      const token = await _getToken();
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    } catch {
      // token acquisition failed — request will proceed without auth header
      // and the backend will return 401, which the UI can handle
    }
  }
  return config;
});

// ── Helper: get auth headers for native fetch (SSE) ───────────────────────────
async function _authHeaders(): Promise<HeadersInit> {
  const headers: HeadersInit = { 'Content-Type': 'application/json' };
  if (_getToken) {
    try {
      const token = await _getToken();
      if (token) {
        (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
      } else {
        console.warn('[SSE auth] no token available for /chat/stream');
      }
    } catch (err) {
      console.warn('[SSE auth] failed to acquire token for /chat/stream', err);
    }
  }
  return headers;
}

// ── Chat (non-streaming) ──────────────────────────────────────────────────────
export async function sendMessage(message: string, thread_id?: string): Promise<ChatResponse> {
  const { data } = await api.post<ChatResponse>('/chat', { message, thread_id });
  return data;
}

// ── SSE streaming types ───────────────────────────────────────────────────────
export type StreamEvent =
  | { type: 'start';    sql_used: string }
  | { type: 'token';    content: string }
  | { type: 'complete'; content: string; sql_used?: string; trace_id?: string }
  | { type: 'metadata'; sql_used: string | null; sql_explanation: string | null;
      chart_data: import('./types').ChartData | null; chart_type: string | null;
      row_preview: Record<string, unknown>[] | null;
      citations: import('./types').Citation[];
      verification: import('./types').VerificationResult | null;
      last_resolver_explanation: string | null;
      answer?: string;          // present on chart_retype (no token stream)
      cache_status?: string;
      trace_id?: string; }
  | { type: 'error';    content: string };

export async function* streamMessage(
  message: string,
  thread_id?: string,
): AsyncGenerator<StreamEvent> {
  const headers = await _authHeaders();

  const response = await fetch('/chat/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify({ message, thread_id }),
  });

  if (!response.ok || !response.body) {
    throw new Error(`Stream request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const raw = line.slice(6).trim();
      if (raw === '[DONE]') return;
      try {
        yield JSON.parse(raw) as StreamEvent;
      } catch { /* skip malformed */ }
    }
  }
}

export async function listThreads(): Promise<ThreadsResponse> {
  const { data } = await api.get<ThreadsResponse>('/threads');
  return data;
}

export async function createThread(thread_id: string): Promise<{ active: string }> {
  const { data } = await api.post('/threads', { thread_id });
  return data;
}

export async function switchThread(thread_id: string): Promise<{ active: string }> {
  const { data } = await api.put(`/threads/${thread_id}/switch`);
  return data;
}

export async function getThreadMessages(thread_id: string): Promise<Record<string, unknown>[]> {
  const { data } = await api.get(`/threads/${thread_id}/messages`);
  return data.messages;
}

export async function clearMemory(): Promise<void> {
  await api.delete('/memory');
}

export async function refreshSchema(): Promise<{ length: number }> {
  const { data } = await api.post('/schema/refresh');
  return data;
}

export async function getSqlCacheEntries(limit = 10): Promise<SqlCacheEntry[]> {
  const { data } = await api.get(`/cache/sql?limit=${limit}`);
  return data.entries;
}

export async function clearSqlCache(): Promise<{ cleared: number }> {
  const { data } = await api.delete('/cache/sql');
  return data;
}

export async function submitFeedback(
  trace_id: string | null | undefined,
  score: 1 | 0,
  comment?: string,
  thread_id?: string,
): Promise<{ ok: boolean; trace_id: string; score_id: string }> {
  try {
    const { data } = await api.post('/feedback', { trace_id, score, comment, thread_id });
    return data;
  } catch (error) {
    if (axios.isAxiosError(error)) {
      const detail = error.response?.data?.detail;
      const message = typeof detail === 'string'
        ? detail
        : `Feedback request failed: ${error.response?.status ?? 'network error'}`;
      throw new Error(message);
    }
    throw error;
  }
}
