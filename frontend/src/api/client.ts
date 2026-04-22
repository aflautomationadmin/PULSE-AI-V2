import axios from 'axios';
import type { ChatResponse, ThreadsResponse, SqlCacheEntry } from './types';

const api = axios.create({ baseURL: '' });

export async function sendMessage(message: string): Promise<ChatResponse> {
  const { data } = await api.post<ChatResponse>('/chat', { message });
  return data;
}

// ── SSE streaming types ───────────────────────────────────────────────────────
export type StreamEvent =
  | { type: 'start';    sql_used: string }
  | { type: 'token';    content: string }
  | { type: 'complete'; content: string; sql_used?: string }
  | { type: 'metadata'; sql_used: string | null; sql_explanation: string | null;
      chart_data: import('./types').ChartData | null; chart_type: string | null;
      row_preview: Record<string, unknown>[] | null;
      citations: import('./types').Citation[];
      verification: import('./types').VerificationResult | null;
      last_resolver_explanation: string | null;
      answer?: string;          // present on chart_retype (no token stream)
      cache_status?: string; }
  | { type: 'error';    content: string };

export async function* streamMessage(message: string): AsyncGenerator<StreamEvent> {
  const response = await fetch('/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
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
