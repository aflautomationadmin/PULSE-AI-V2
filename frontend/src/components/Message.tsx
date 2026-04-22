import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatResponse } from '../api/types';
import { ChartView } from './ChartView';

export interface MessageItem {
  id: string;
  role: 'user' | 'bot';
  text: string;
  response?: ChatResponse;
  timestamp: Date;
  streaming?: boolean;   // true while tokens are still arriving
}

interface Props {
  message: MessageItem;
}

type Feedback = 'up' | 'down' | null;

export function Message({ message }: Props) {
  const [feedback, setFeedback] = useState<Feedback>(null);
  const isUser = message.role === 'user';
  const r = message.response;

  if (isUser) {
    return (
      <div className="msg-row user">
        <div className="msg-avatar user-avatar">
          {/* User initials placeholder */}
          U
        </div>
        <div className="msg-col" style={{ textAlign: 'right' }}>
          <div className="msg-bubble">{message.text}</div>
          <div className="msg-meta" style={{ justifyContent: 'flex-end' }}>
            <span className="msg-time">
              {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="msg-row bot">
      <img
        src="/digitalization.png"
        alt="bot"
        className="msg-avatar"
      />
      <div className="msg-col">
        {/* Main text bubble */}
        <div className="msg-bubble">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
          {message.streaming && <span className="streaming-cursor" />}
        </div>

        {r && (
          <>
            {/* SQL plain-English explanation + cache badge */}
            {r.sql_explanation && (
              <div className="sql-explain">
                <span className="tag">SQL</span>
                {r.sql_explanation}
                {r.cache_status && r.cache_status !== 'null' && (
                  <span
                    className={`cache-badge ${r.cache_status.startsWith('semantic') ? 'cache-badge--semantic' : r.cache_status.startsWith('kpi') ? 'cache-badge--kpi' : 'cache-badge--exact'}`}
                    title={`Cache: ${r.cache_status}`}
                  >
                    {r.cache_status === 'exact'
                      ? '⚡ cached'
                      : r.cache_status.startsWith('semantic')
                        ? `⚡ ~similar (${r.cache_status.split(':')[1]})`
                        : r.cache_status.startsWith('kpi')
                          ? `⚡ ${r.cache_status.split(':')[1]} procedure`
                          : `⚡ ${r.cache_status}`}
                  </span>
                )}
              </div>
            )}

            {/* Verification */}
            {r.verification && (
              r.verification.verified ? (
                <div className="verify-ok">
                  <span className="verify-icon">✓</span>
                  All numbers verified against source data
                </div>
              ) : (
                <div className="verify-warn">
                  <span className="verify-icon">⚠</span>
                  <div>
                    {r.verification.issues.length} verification issue(s)
                    {r.verification.issues.map((iss, i) => (
                      <span key={i} className="verify-issue">· {iss.issue}</span>
                    ))}
                  </div>
                </div>
              )
            )}

            {/* Chart — rendered client-side, no file I/O */}
            {r.chart_data && <ChartView data={r.chart_data} />}

            {/* Data preview */}
            {r.row_preview && r.row_preview.length > 0 && (
              <details className="info-panel" style={{ marginTop: 6 }}>
                <summary>
                  <span className="chevron">▶</span>
                  Preview data ({r.row_preview.length} rows)
                </summary>
                <div className="info-panel-body">
                  <div className="data-table-wrap">
                    <table className="data-table">
                      <thead>
                        <tr>
                          {Object.keys(r.row_preview[0]).map(col => (
                            <th key={col}>{col}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {r.row_preview.map((row, i) => (
                          <tr key={i}>
                            {Object.values(row).map((val, j) => (
                              <td key={j}>{String(val ?? '')}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </details>
            )}

            {/* Citations */}
            {r.citations && r.citations.length > 0 && (
              <details className="info-panel" style={{ marginTop: 6 }}>
                <summary>
                  <span className="chevron">▶</span>
                  Sources ({r.citations.length} citations)
                </summary>
                <div className="info-panel-body">
                  {r.citations.map((c, i) => (
                    <div key={i} className="citation-item">
                      <div className="citation-claim">{c.claim}</div>
                      <div className="citation-source">
                        <span>{c.source_column}</span>={c.source_value}
                        {' → '}
                        <span>{c.metric_column}</span>: {c.metric_value}
                        <span style={{ opacity: 0.5 }}> (row {c.row_index + 1})</span>
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            )}

            {/* SQL */}
            {r.last_sql && (
              <details className="info-panel" style={{ marginTop: 6 }}>
                <summary>
                  <span className="chevron">▶</span>
                  View SQL
                </summary>
                <div className="info-panel-body">
                  <pre className="sql-code">{r.last_sql}</pre>
                </div>
              </details>
            )}

            {/* Resolver explanation */}
            {r.last_resolver_explanation && (
              <div className="resolver-note">
                <span>⚙</span>
                {r.last_resolver_explanation}
              </div>
            )}
          </>
        )}

        {/* Meta row: time + feedback */}
        <div className="msg-meta">
          <span className="msg-time">
            {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
          {r && (
            <div className="feedback-bar">
              <button
                className={`feedback-btn${feedback === 'up' ? ' selected-up' : ''}`}
                title="Helpful"
                onClick={() => setFeedback(f => f === 'up' ? null : 'up')}
              >👍</button>
              <button
                className={`feedback-btn${feedback === 'down' ? ' selected-down' : ''}`}
                title="Not helpful"
                onClick={() => setFeedback(f => f === 'down' ? null : 'down')}
              >👎</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
