// Sandbox.jsx — Backtesting sandbox interface.
import { useEffect, useState } from 'react';
import { Play, BarChart3 } from 'lucide-react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { api } from '../api/client';
import './Sandbox.css';

function formatMoney(n) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
}

function formatPct(n) {
  if (n == null) return '—';
  return `${(n * 100).toFixed(2)}%`;
}

export default function Sandbox() {
  const [available, setAvailable] = useState([]);
  const [models, setModels] = useState([]);
  const [ticker, setTicker] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [startingCash, setStartingCash] = useState('10000');
  const [confidence, setConfidence] = useState('0.5');
  const [modelId, setModelId] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.getSandboxAvailable().then(setAvailable).catch(() => {});
    api.getPredictionModels().then(setModels).catch(() => {});
  }, []);

  async function handleRun(e) {
    e.preventDefault();
    if (!ticker) return;
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const payload = {
        ticker: ticker.toUpperCase(),
        start_date: startDate || null,
        end_date: endDate || null,
        starting_cash: parseFloat(startingCash),
        confidence_threshold: parseFloat(confidence),
        model_version_id: modelId ? parseInt(modelId, 10) : null,
      };
      const res = await api.runSandboxBacktest(payload);
      setResult(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const selected = available.find((a) => a.ticker === ticker);

  return (
    <Layout>
      <Topbar />
      <div className="page-head">
        <div>
          <h1 className="page-title">Backtesting Sandbox</h1>
          <p className="page-sub">Replay the paper strategy over any historical window, against buy-and-hold.</p>
        </div>
      </div>

      <div className="sandbox-grid">
        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Parameters</h3>
          </div>
          <form onSubmit={handleRun} className="sandbox-form">
            <div className="form-row">
              <label>Ticker</label>
              <select value={ticker} onChange={(e) => setTicker(e.target.value)} required>
                <option value="">Select a ticker</option>
                {available.map((a) => (
                  <option key={a.ticker} value={a.ticker}>
                    {a.ticker} ({a.trading_days} days, {a.predictions} predictions)
                  </option>
                ))}
              </select>
            </div>

            {selected && (
              <p className="sandbox-range-hint">
                Data: {selected.first_date} → {selected.last_date}
              </p>
            )}

            <div className="form-row">
              <label>Start date</label>
              <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
            </div>

            <div className="form-row">
              <label>End date</label>
              <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
            </div>

            <div className="form-row">
              <label>Starting cash</label>
              <input
                type="number"
                min="100"
                step="100"
                value={startingCash}
                onChange={(e) => setStartingCash(e.target.value)}
              />
            </div>

            <div className="form-row">
              <label>Confidence threshold</label>
              <input
                type="number"
                min="0.5"
                max="1"
                step="0.05"
                value={confidence}
                onChange={(e) => setConfidence(e.target.value)}
              />
              <span className="form-hint">Only act on predictions with confidence ≥ this value</span>
            </div>

            <div className="form-row">
              <label>Model version</label>
              <select value={modelId} onChange={(e) => setModelId(e.target.value)}>
                <option value="">All models</option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>{m.name} ({m.feature_set})</option>
                ))}
              </select>
            </div>

            <button className="btn btn-primary" type="submit" disabled={loading || !ticker}>
              <Play size={15} /> {loading ? 'Running…' : 'Run backtest'}
            </button>
          </form>
        </div>

        <div className="sandbox-results">
          {error && <div className="alert-error">{error}</div>}

          {result && (
            <>
              <div className="card">
                <div className="card-head">
                  <h3 className="card-title">Strategy vs. Buy-and-Hold</h3>
                  <span className="badge">{result.ticker}</span>
                </div>
                <div className="sandbox-stats">
                  <div className="sandbox-stat">
                    <span className="sandbox-stat-label">Strategy return</span>
                    <span className={`sandbox-stat-value ${result.strategy_return >= 0 ? 'gain' : 'loss'}`}>
                      {formatPct(result.strategy_return)}
                    </span>
                  </div>
                  <div className="sandbox-stat">
                    <span className="sandbox-stat-label">Buy-and-hold return</span>
                    <span className={`sandbox-stat-value ${result.buy_hold_return >= 0 ? 'gain' : 'loss'}`}>
                      {formatPct(result.buy_hold_return)}
                    </span>
                  </div>
                  <div className="sandbox-stat">
                    <span className="sandbox-stat-label">Excess return</span>
                    {(() => {
                      const excess = result.strategy_return != null && result.buy_hold_return != null
                        ? result.strategy_return - result.buy_hold_return : null;
                      return (
                        <span className={`sandbox-stat-value ${excess == null ? '' : excess >= 0 ? 'gain' : 'loss'}`}>
                          {formatPct(excess)}
                        </span>
                      );
                    })()}
                  </div>
                  <div className="sandbox-stat">
                    <span className="sandbox-stat-label">Final value</span>
                    <span className="sandbox-stat-value">{formatMoney(result.final_value)}</span>
                  </div>
                  <div className="sandbox-stat">
                    <span className="sandbox-stat-label">Win rate</span>
                    <span className="sandbox-stat-value">{formatPct(result.win_rate)}</span>
                  </div>
                  <div className="sandbox-stat">
                    <span className="sandbox-stat-label">Strategy Sharpe</span>
                    <span className="sandbox-stat-value">{result.strategy_sharpe != null ? result.strategy_sharpe.toFixed(2) : '—'}</span>
                  </div>
                </div>
              </div>

              <div className="card">
                <div className="card-head">
                  <h3 className="card-title">Trade log</h3>
                  <span className="badge">{result.trades?.length || 0} trades</span>
                </div>
                {result.trades?.length ? (
                  <div className="sandbox-trades">
                    {result.trades.map((t, i) => (
                      <div key={i} className="sandbox-trade-row">
                        <span className={`trade-action ${t.action}`}>{t.action.toUpperCase()}</span>
                        <span className="trade-ticker">{t.ticker}</span>
                        <span className="trade-shares">{t.shares} shares</span>
                        <span className="trade-price">{formatMoney(t.price)}</span>
                        <span className="trade-date">{t.date}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="empty-state">No trades triggered in this window.</p>
                )}
              </div>

              <div className="card">
                <div className="card-head">
                  <h3 className="card-title">Daily equity curve</h3>
                </div>
                <div className="sandbox-equity">
                  {result.equity_curve?.slice(-30).map((p, i) => (
                    <div key={i} className="equity-bar" title={`${p.date}: ${formatMoney(p.strategy_value)}`}>
                      <div
                        className="equity-bar-fill"
                        style={{
                          height: `${Math.max(2, ((p.strategy_value - result.starting_cash) / result.starting_cash) * 100 + 20)}px`,
                          background: p.strategy_value >= result.starting_cash ? 'var(--color-gain)' : 'var(--color-loss)',
                        }}
                      />
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {!result && !error && (
            <div className="card sandbox-empty">
              <BarChart3 size={32} />
              <p>Pick a ticker and date range, then run the backtest to see how the strategy would have performed.</p>
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
}
