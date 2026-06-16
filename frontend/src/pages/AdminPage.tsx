import { useState, useEffect, useCallback } from 'react';
import { getAdminUsers, getAdminConversations } from '../api/client';
import type { AdminUser, AdminThread } from '../api/types';

interface AdminPageProps {
  onClose: () => void;
}

function formatWhen(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export default function AdminPage({ onClose }: AdminPageProps) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [threads, setThreads] = useState<AdminThread[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [loadingConvos, setLoadingConvos] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');

  useEffect(() => {
    (async () => {
      try {
        setUsers(await getAdminUsers());
      } catch {
        setError('Failed to load users. You may not have admin access.');
      } finally {
        setLoadingUsers(false);
      }
    })();
  }, []);

  const openUser = useCallback(async (userId: string) => {
    setSelected(userId);
    setLoadingConvos(true);
    setThreads([]);
    try {
      setThreads(await getAdminConversations(userId));
    } catch {
      setError('Failed to load conversations for this user.');
    } finally {
      setLoadingConvos(false);
    }
  }, []);

  const filteredUsers = users.filter(u =>
    u.user_id.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="admin-overlay">
      <div className="admin-shell">
        {/* Header */}
        <header className="admin-header">
          <div className="admin-title">
            🛡️ Admin Portal
            <span className="admin-subtitle">Conversation monitoring · Arvind Fashions</span>
          </div>
          <button className="admin-close-btn" onClick={onClose}>← Back to chat</button>
        </header>

        {error && <div className="admin-error">{error}</div>}

        <div className="admin-body">
          {/* User list */}
          <aside className="admin-users">
            <input
              className="admin-search"
              placeholder="Search users…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            {loadingUsers && <div className="admin-empty">Loading users…</div>}
            {!loadingUsers && filteredUsers.length === 0 && (
              <div className="admin-empty">No users found.</div>
            )}
            {filteredUsers.map(u => (
              <button
                key={u.user_id}
                className={`admin-user-row ${selected === u.user_id ? 'active' : ''}`}
                onClick={() => openUser(u.user_id)}
              >
                <span className="admin-user-avatar">{u.user_id.charAt(0).toUpperCase()}</span>
                <span className="admin-user-meta">
                  <span className="admin-user-email">{u.user_id}</span>
                  <span className="admin-user-stats">
                    {u.turn_count} msgs · {u.thread_count} threads · {formatWhen(u.last_activity)}
                  </span>
                </span>
              </button>
            ))}
          </aside>

          {/* Conversation detail */}
          <section className="admin-detail">
            {!selected && (
              <div className="admin-empty admin-detail-empty">
                Select a user to view their questions and the bot's responses.
              </div>
            )}
            {selected && loadingConvos && <div className="admin-empty">Loading conversations…</div>}
            {selected && !loadingConvos && threads.length === 0 && (
              <div className="admin-empty">No conversations for this user.</div>
            )}
            {selected && !loadingConvos && threads.map(thread => (
              <div key={thread.thread_id} className="admin-thread">
                <div className="admin-thread-head">
                  <span className="admin-thread-id">{thread.thread_id}</span>
                  <span className="admin-thread-when">{formatWhen(thread.updated_at)}</span>
                </div>
                {thread.turns.map((turn, i) => (
                  <div key={i} className="admin-turn">
                    <div className="admin-q">
                      <span className="admin-q-label">Q</span>
                      <span className="admin-q-text">{turn.user}</span>
                      {turn.created_at && (
                        <span className="admin-turn-when">{formatWhen(turn.created_at)}</span>
                      )}
                    </div>
                    <div className="admin-a">
                      <span className="admin-a-label">A</span>
                      <div className="admin-a-body">
                        <div className="admin-a-text">{turn.assistant}</div>
                        {turn.sql_used && (
                          <details className="admin-sql">
                            <summary>
                              <span className={`admin-route route-${turn.route}`}>{turn.route}</span>
                              View SQL / procedure
                            </summary>
                            <pre>{turn.sql_used}</pre>
                            {turn.sql_explanation && (
                              <div className="admin-sql-expl">{turn.sql_explanation}</div>
                            )}
                          </details>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </section>
        </div>
      </div>
    </div>
  );
}
