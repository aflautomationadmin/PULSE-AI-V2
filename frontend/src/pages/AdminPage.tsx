import { useState, useEffect, useCallback } from 'react';
import {
  getAdminUsers, getAdminConversations, getAdminUsage,
  getAdmins, grantAdmin, revokeAdmin,
} from '../api/client';
import type { AdminUser, AdminThread, AdminUsageSummary, AdminRole } from '../api/types';

interface AdminPageProps {
  onClose: () => void;
}

type AdminTab = 'conversations' | 'usage' | 'access';

function formatWhen(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function fmtTokens(n: number): string {
  return n.toLocaleString();
}

function fmtCost(usd: number): string {
  // costs are tiny — show up to 4 significant decimals, min $0.0001
  if (usd === 0) return '$0';
  if (usd < 0.0001) return '<$0.0001';
  return '$' + usd.toFixed(4);
}

function UsageView() {
  const [summary, setSummary] = useState<AdminUsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        setSummary(await getAdminUsage());
      } catch {
        setErr('Failed to load usage data.');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <div className="admin-empty">Loading usage…</div>;
  if (err) return <div className="admin-empty">{err}</div>;
  if (!summary) return null;

  const { by_agent, by_user, totals } = summary;
  const maxAgentTokens = Math.max(1, ...by_agent.map(a => a.total_tokens));

  return (
    <div className="admin-usage">
      {/* Totals */}
      <div className="usage-cards">
        <div className="usage-card">
          <span className="usage-card-value">{fmtTokens(totals.total_tokens)}</span>
          <span className="usage-card-label">Total tokens</span>
        </div>
        <div className="usage-card">
          <span className="usage-card-value">{fmtCost(totals.cost)}</span>
          <span className="usage-card-label">Est. cost</span>
        </div>
        <div className="usage-card">
          <span className="usage-card-value">{fmtTokens(totals.prompt_tokens)}</span>
          <span className="usage-card-label">Prompt tokens</span>
        </div>
        <div className="usage-card">
          <span className="usage-card-value">{fmtTokens(totals.completion_tokens)}</span>
          <span className="usage-card-label">Completion tokens</span>
        </div>
        <div className="usage-card">
          <span className="usage-card-value">{totals.tracked_turns}/{totals.turns}</span>
          <span className="usage-card-label">Tracked turns</span>
        </div>
      </div>

      {/* Per-agent breakdown */}
      <h3 className="usage-section-title">Token consumption by pipeline agent</h3>
      {by_agent.length === 0 && (
        <div className="admin-empty">
          No token data yet. Tokens are recorded for new questions going forward.
        </div>
      )}
      {by_agent.length > 0 && (
        <table className="usage-table">
          <thead>
            <tr>
              <th>Agent</th><th>Calls</th><th>Prompt</th>
              <th>Completion</th><th>Total tokens</th><th>Cost</th><th></th>
            </tr>
          </thead>
          <tbody>
            {by_agent.map(a => (
              <tr key={a.agent}>
                <td className="usage-agent">{a.agent}</td>
                <td>{a.calls}</td>
                <td>{fmtTokens(a.prompt_tokens)}</td>
                <td>{fmtTokens(a.completion_tokens)}</td>
                <td><strong>{fmtTokens(a.total_tokens)}</strong></td>
                <td>{fmtCost(a.cost)}</td>
                <td className="usage-bar-cell">
                  <span
                    className="usage-bar"
                    style={{ width: `${(a.total_tokens / maxAgentTokens) * 100}%` }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Per-user breakdown */}
      {by_user.length > 0 && (
        <>
          <h3 className="usage-section-title">Consumption by user</h3>
          <table className="usage-table">
            <thead>
              <tr><th>User</th><th>Turns</th><th>Total tokens</th><th>Cost</th></tr>
            </thead>
            <tbody>
              {by_user.map(u => (
                <tr key={u.user_id}>
                  <td className="usage-agent">{u.user_id}</td>
                  <td>{u.turns}</td>
                  <td><strong>{fmtTokens(u.total_tokens)}</strong></td>
                  <td>{fmtCost(u.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function AccessView() {
  const [admins, setAdmins] = useState<AdminRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setAdmins(await getAdmins());
    } catch {
      setMsg('Failed to load admin list.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function handleGrant(e: React.FormEvent) {
    e.preventDefault();
    const clean = email.trim().toLowerCase();
    if (!clean) return;
    setBusy(true);
    setMsg(null);
    try {
      await grantAdmin(clean);
      setEmail('');
      setMsg(`✓ ${clean} now has admin access.`);
      await load();
    } catch {
      setMsg(`✗ Could not grant access to ${clean}. Check the email and that MongoDB is running.`);
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(target: string) {
    setBusy(true);
    setMsg(null);
    try {
      await revokeAdmin(target);
      setMsg(`✓ Admin access removed for ${target}.`);
      await load();
    } catch {
      setMsg(`✗ Could not remove ${target}.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-access">
      <h3 className="usage-section-title">Grant admin access</h3>
      <form className="access-grant" onSubmit={handleGrant}>
        <input
          className="admin-search access-input"
          type="email"
          placeholder="user@arvindfashions.com"
          value={email}
          onChange={e => setEmail(e.target.value)}
          disabled={busy}
        />
        <button className="access-grant-btn" type="submit" disabled={busy || !email.trim()}>
          + Grant access
        </button>
      </form>
      {msg && <div className="access-msg">{msg}</div>}

      <h3 className="usage-section-title">Current admins</h3>
      {loading && <div className="admin-empty">Loading…</div>}
      {!loading && (
        <table className="usage-table">
          <thead>
            <tr><th>Email</th><th>Type</th><th>Granted by</th><th></th></tr>
          </thead>
          <tbody>
            {admins.map(a => (
              <tr key={a.email}>
                <td className="usage-agent">{a.email}</td>
                <td>
                  {a.removable
                    ? <span className="access-badge granted">Granted</span>
                    : <span className="access-badge permanent">Permanent</span>}
                </td>
                <td>{a.granted_by ?? '—'}</td>
                <td>
                  {a.removable ? (
                    <button
                      className="access-revoke-btn"
                      disabled={busy}
                      onClick={() => handleRevoke(a.email)}
                    >
                      Remove
                    </button>
                  ) : (
                    <span className="access-locked" title="Permanent admin — cannot be removed">🔒</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function AdminPage({ onClose }: AdminPageProps) {
  const [tab, setTab] = useState<AdminTab>('conversations');
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
          <div className="admin-tabs">
            <button
              className={`admin-tab ${tab === 'conversations' ? 'active' : ''}`}
              onClick={() => setTab('conversations')}
            >
              💬 Conversations
            </button>
            <button
              className={`admin-tab ${tab === 'usage' ? 'active' : ''}`}
              onClick={() => setTab('usage')}
            >
              📊 Token Usage
            </button>
            <button
              className={`admin-tab ${tab === 'access' ? 'active' : ''}`}
              onClick={() => setTab('access')}
            >
              👥 Access
            </button>
          </div>
          <button className="admin-close-btn" onClick={onClose}>← Back to chat</button>
        </header>

        {error && <div className="admin-error">{error}</div>}

        {tab === 'usage' && (
          <div className="admin-detail" style={{ overflowY: 'auto' }}>
            <UsageView />
          </div>
        )}

        {tab === 'access' && (
          <div className="admin-detail" style={{ overflowY: 'auto' }}>
            <AccessView />
          </div>
        )}

        {tab === 'conversations' && (
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
        )}
      </div>
    </div>
  );
}
