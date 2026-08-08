// Leaderboard.jsx — Model leaderboard page.
import { useEffect, useState } from 'react';
import { Trophy, TrendingUp, TrendingDown, Target, Activity } from 'lucide-react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { api } from '../api/client';
import './Leaderboard.css';

function formatPct(n) {
  if (n == null) return '—';
  return `${(n * 100).toFixed(1)}%`;
}

function formatNum(n, digits = 3) {
  if (n == null) return '—';
  return n.toFixed(digits);
}

export default function Leaderboard() {
  const [board, setBoard] = useState(null);
  const [ticker, setTicker] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  async function load(t) {
    setLoading(true);
    setError('');
    try {
      const data = await api.getLeaderboard(t || null);
      setBoard(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function handleFilter(e) {
    e.preventDefault();
    load(ticker.trim().toUpperCase() || null);
  }

  return (
    <Layout>
      <Topbar />
      <div className="page-head">
        <div>
          <h1 className="page-title">Model Leaderboard</h1>
          <p className="page-sub">
            Live accuracy of every registered model version, scored only on predictions made before their outcome was known.
          </p>
        </div>
        <form className="leaderboard-filter" onSubmit={handleFilter}>
          <input
            placeholder="Filter by ticker"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
          />
          <button className="btn btn-secondary" type="submit">Apply</button>
        </form>
      </div>

      {error && <div className="alert-error">{error}</div>}

      {loading ? (
        <p className="empty-state">Loading…</p>
      ) : !board?.models?.length ? (
        <p className="empty-state">No models registered yet. Train one with ml/train_lstm.py.</p>
      ) : (
        <div className="leaderboard-grid">
          {board.models.map((m, i) => (
            <div key={m.model_version_id} className={`card leaderboard-card ${i === 0 ? 'leaderboard-card-first' : ''}`}>
              <div className="card-head">
                <div className="leaderboard-rank">
                  {i === 0 && <Trophy size={16} className="leaderboard-trophy" />}
                  <span className="leaderboard-rank-num">#{i + 1}</span>
                </div>
                <h3 className="card-title">{m.name}</h3>
                <span className={`badge ${m.is_active ? 'badge-gain' : ''}`}>
                  {m.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>

              <p className="leaderboard-desc">{m.description}</p>

              <div className="leaderboard-stats">
                <div className="leaderboard-stat">
                  <Target size={14} />
                  <span className="leaderboard-stat-label">Live accuracy</span>
                  <span className={`leaderboard-stat-value ${m.live_accuracy != null && m.live_accuracy >= 0.5 ? 'gain' : 'loss'}`}>
                    {formatPct(m.live_accuracy)}
                  </span>
                </div>
                <div className="leaderboard-stat">
                  <Activity size={14} />
                  <span className="leaderboard-stat-label">Test accuracy</span>
                  <span className="leaderboard-stat-value">{formatPct(m.test_accuracy)}</span>
                </div>
                <div className="leaderboard-stat">
                  <TrendingUp size={14} />
                  <span className="leaderboard-stat-label">Test Sharpe</span>
                  <span className="leaderboard-stat-value">{formatNum(m.test_sharpe)}</span>
                </div>
                <div className="leaderboard-stat">
                  <TrendingDown size={14} />
                  <span className="leaderboard-stat-label">Brier score</span>
                  <span className="leaderboard-stat-value">{formatNum(m.brier_score)}</span>
                </div>
              </div>

              <div className="leaderboard-meta">
                <span>Feature set: <strong>{m.feature_set}</strong></span>
                <span>Trained: {m.trained_at ? new Date(m.trained_at).toLocaleDateString() : '—'}</span>
                <span>Resolved: {m.resolved_predictions} / {m.total_predictions}</span>
              </div>

              {m.expected_calibration_error != null && (
                <div className="leaderboard-calibration">
                  <span className="leaderboard-cal-label">Calibration error</span>
                  <div className="leaderboard-cal-bar">
                    <div
                      className="leaderboard-cal-fill"
                      style={{ width: `${Math.min(100, m.expected_calibration_error * 100)}%` }}
                    />
                  </div>
                  <span className="leaderboard-cal-value">{formatPct(m.expected_calibration_error)}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {board?.note && <p className="leaderboard-note">{board.note}</p>}
    </Layout>
  );
}
