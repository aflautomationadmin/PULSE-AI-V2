import { useState } from 'react';
import type { Thread } from '../api/types';
import { createThread, switchThread, clearMemory, refreshSchema, clearSqlCache, getThreadMessages } from '../api/client';

interface Props {
  threads: Thread[];
  activeThread: string;
  onThreadChange: (threadId?: string, messages?: Record<string, unknown>[]) => void;
  onNotify: (msg: string) => void;
}

function generateThreadId(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `thread-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

export function Sidebar({ threads, activeThread, onThreadChange, onNotify }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const [busy, setBusy] = useState('');

  async function handleSwitch(id: string) {
    setBusy(id);
    try {
      await switchThread(id);
      const messages = await getThreadMessages(id);
      onThreadChange(id, messages);
    } finally { setBusy(''); }
  }

  async function handleCreate() {
    const id = generateThreadId();
    setBusy('new');
    try {
      await createThread(id);
      onThreadChange(id, []);
      onNotify(`New thread "${id}" created`);
    } catch { onNotify('Failed to create thread'); }
    finally { setBusy(''); }
  }

  async function handleClearMemory() {
    await clearMemory();
    onThreadChange(undefined, []);
    onNotify('Memory cleared.');
  }

  async function handleRefresh() {
    setBusy('schema');
    try { const r = await refreshSchema(); onNotify(`Schema refreshed (${r.length.toLocaleString()} chars).`); }
    finally { setBusy(''); }
  }

  async function handleClearCache() {
    const r = await clearSqlCache();
    onNotify(`Cleared ${r.cleared} SQL cache entries.`);
  }

  return (
    <aside
      className={`sidebar${collapsed ? ' collapsed' : ''}`}
      onClick={collapsed ? () => setCollapsed(false) : undefined}
    >
      {/* Header */}
      <div className="sidebar-header">
        <img src="/LOgo.png" alt="Arvind Fashions" className="sidebar-logo" />
        <div className="sidebar-brand">
          AI-DA-Agents
          <span>Arvind Fashions</span>
        </div>
        <button className="sidebar-toggle" onClick={() => setCollapsed(v => !v)} title={collapsed ? 'Expand' : 'Collapse'}>
          {collapsed ? '›' : '‹'}
        </button>
      </div>

      {/* New thread button */}
      <button className="new-thread-btn" onClick={handleCreate} disabled={busy === 'new'}>
        <span className="icon">{busy === 'new' ? '…' : '＋'}</span>
        <span className="label">New Thread</span>
      </button>

      {/* Thread list */}
      {!collapsed && <div className="thread-section-label">Conversations</div>}
      <div className="thread-list">
        {threads.map(t => (
          <div
            key={t.thread_id}
            className={`thread-item${t.is_active ? ' active' : ''}`}
            onClick={() => handleSwitch(t.thread_id)}
            title={t.thread_id}
          >
            <span className="thread-dot" />
            <span className="thread-name">{t.thread_id}</span>
            <span className="thread-turns">{t.turn_count}t</span>
          </div>
        ))}
      </div>

      {/* Footer actions */}
      <div className="sidebar-footer">
        <button className="sidebar-action" onClick={handleRefresh} disabled={busy === 'schema'}>
          <span className="icon">↻</span>
          <span className="label">{busy === 'schema' ? 'Refreshing…' : 'Refresh Schema'}</span>
        </button>
        <button className="sidebar-action" onClick={handleClearMemory}>
          <span className="icon">🗑</span>
          <span className="label">Clear Memory</span>
        </button>
        <button className="sidebar-action" onClick={handleClearCache}>
          <span className="icon">⌫</span>
          <span className="label">Clear SQL Cache</span>
        </button>
      </div>
    </aside>
  );
}
