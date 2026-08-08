// Portfolio.jsx — Portfolio analytics and holdings page.
import { useEffect, useMemo, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  PieChart, Pie, Cell,
} from 'recharts';
import { ArrowUpRight, Plus } from 'lucide-react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { api } from '../api/client';
import './Portfolio.css';

const ALLOCATION_COLORS = ['#3ee08b', '#9b8cf9', '#5fd4a8', '#7a6cd6', '#2a9e6a', '#c3b8fb'];

function formatMoney(n, opts = {}) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2, ...opts });
}

function formatPct(n, digits = 1) {
  if (n == null) return '—';
  return `${(n * 100).toFixed(digits)}%`;
}

function formatNum(n, digits = 2) {
  if (n == null) return '—';
  return n.toFixed(digits);
}

function Sparkline({ points }) {
  if (!points || points.length < 2) return <span className="empty-state" style={{ padding: 0 }}>—</span>;
  const w = 88;
  const h = 28;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const step = w / (points.length - 1);
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${(i * step).toFixed(1)} ${(h - ((p - min) / range) * h).toFixed(1)}`).join(' ');
  const up = points[points.length - 1] >= points[0];
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
      <path d={d} fill="none" stroke={up ? 'var(--color-gain)' : 'var(--color-loss)'} strokeWidth="1.75" />
    </svg>
  );
}

export default function Portfolio() {
  const [portfolio, setPortfolio] = useState(null);
  const [history, setHistory] = useState([]);
  const [benchmark, setBenchmark] = useState(null);
  const [benchmarkSeries, setBenchmarkSeries] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [sparklines, setSparklines] = useState({});
  const [transactions, setTransactions] = useState([]);
  const [tradeTicker, setTradeTicker] = useState('');
  const [tradeShares, setTradeShares] = useState('');
  const [tradeError, setTradeError] = useState('');
  const [tradeBusy, setTradeBusy] = useState(false);

  async function loadAll() {
    const [portfolioResult, historyResult, benchmarkResult, analyticsResult, txResult] = await Promise.allSettled([
      api.getPortfolio(),
      api.getPortfolioHistory(),
      api.getBenchmarkComparison(),
      api.getPortfolioAnalytics(),
      api.getTransactions(50),
    ]);
    if (portfolioResult.status === 'fulfilled') setPortfolio(portfolioResult.value);
    if (historyResult.status === 'fulfilled') setHistory(historyResult.value);
    if (benchmarkResult.status === 'fulfilled') setBenchmark(benchmarkResult.value);
    if (analyticsResult.status === 'fulfilled') setAnalytics(analyticsResult.value);
    if (txResult.status === 'fulfilled') setTransactions(txResult.value);

    if (portfolioResult.status === 'fulfilled') {
      const tickers = portfolioResult.value.holdings.map((h) => h.ticker);
      const results = await Promise.allSettled(tickers.map((t) => api.getPriceHistory(t, 30)));
      const map = {};
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') map[tickers[i]] = r.value.candles.map((c) => c.close).filter((v) => v != null);
      });
      setSparklines(map);
    }
  }

  useEffect(() => {
    loadAll();
    api.getPriceHistory('SPY', 365).then((r) => setBenchmarkSeries(r.candles)).catch(() => {});
  }, []);

  const comparisonChart = useMemo(() => {
    if (!history.length) return [];
    const spyByDate = new Map(benchmarkSeries.map((c) => [c.date, c.close]));
    const firstPortfolio = history[0].total_value;
    let firstSpy = null;
    for (const snap of history) {
      if (spyByDate.has(snap.date)) { firstSpy = spyByDate.get(snap.date); break; }
    }
    return history.map((snap) => {
      const spyClose = spyByDate.get(snap.date);
      return {
        date: snap.date,
        portfolio: ((snap.total_value - firstPortfolio) / firstPortfolio) * 100,
        spy: firstSpy && spyClose ? ((spyClose - firstSpy) / firstSpy) * 100 : null,
      };
    });
  }, [history, benchmarkSeries]);

  const allocation = useMemo(() => {
    if (!portfolio?.holdings?.length) return [];
    return portfolio.holdings
      .filter((h) => h.market_value != null)
      .sort((a, b) => b.market_value - a.market_value)
      .map((h) => ({ name: h.ticker, value: h.market_value }));
  }, [portfolio]);

  async function handleTrade(action) {
    setTradeError('');
    const shares = parseFloat(tradeShares);
    if (!tradeTicker.trim() || !shares || shares <= 0) {
      setTradeError('Enter a ticker and a positive number of shares.');
      return;
    }
    setTradeBusy(true);
    try {
      if (action === 'buy') await api.buy(tradeTicker, shares);
      else await api.sell(tradeTicker, shares);
      setTradeTicker('');
      setTradeShares('');
      await loadAll();
    } catch (err) {
      setTradeError(err.message);
    } finally {
      setTradeBusy(false);
    }
  }

  return (
    <Layout>
      <Topbar />

      <div className="pred-head">
        <div>
          <h1 className="page-title">Portfolio</h1>
          <p className="page-sub">Paper trading only — real prices, fake money, honest results.</p>
        </div>
      </div>

      <div className="port-grid">
        <div className="port-col-main">
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Value vs. S&amp;P 500</h3>
              {benchmark?.benchmark_return != null && (
                <div className="pill-row">
                  <span className={`badge ${benchmark.portfolio_return >= 0 ? 'badge-gain' : 'badge-loss'}`}>
                    You {(benchmark.portfolio_return * 100).toFixed(2)}%
                  </span>
                  <span className={`badge ${benchmark.benchmark_return >= 0 ? 'badge-gain' : 'badge-loss'}`}>
                    SPY {(benchmark.benchmark_return * 100).toFixed(2)}%
                  </span>
                </div>
              )}
            </div>
            {comparisonChart.length > 1 ? (
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={comparisonChart}>
                  <CartesianGrid vertical={false} stroke="var(--color-border-soft)" />
                  <XAxis dataKey="date" stroke="var(--color-text-faint)" fontSize={11} tickLine={false} axisLine={false} minTickGap={40} />
                  <YAxis stroke="var(--color-text-faint)" fontSize={11} tickLine={false} axisLine={false} tickFormatter={(v) => `${v.toFixed(0)}%`} width={44} />
                  <Tooltip
                    contentStyle={{ background: 'var(--color-surface-raised)', border: '1px solid var(--color-border)', borderRadius: 10, fontSize: 12 }}
                    formatter={(v, n) => [`${v?.toFixed(2)}%`, n === 'portfolio' ? 'Your portfolio' : 'S&P 500']}
                  />
                  <Line type="monotone" dataKey="portfolio" stroke="var(--color-accent)" strokeWidth={2.5} dot={false} />
                  <Line type="monotone" dataKey="spy" stroke="var(--color-secondary)" strokeWidth={2} dot={false} strokeDasharray="4 3" />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <p className="empty-state">Not enough daily snapshots yet to compare against SPY.</p>
            )}
          </div>

          {portfolio?.holdings?.length > 0 && (
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">Holdings</h3>
              </div>
              <table className="holdings-table">
                <thead>
                  <tr>
                    <th>Ticker</th><th>Shares</th><th>Avg cost</th><th>Price</th>
                    <th>30d trend</th><th>Value</th><th>P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {portfolio.holdings.map((h) => (
                    <tr key={h.ticker}>
                      <td>
                        <div className="holdings-ticker">
                          <div className="ticker-chip">{h.ticker.slice(0, 2)}</div>
                          <span className="mono">{h.ticker}</span>
                        </div>
                      </td>
                      <td className="mono">{h.shares}</td>
                      <td className="mono">{formatMoney(h.avg_cost_basis)}</td>
                      <td className="mono">{h.current_price != null ? formatMoney(h.current_price) : '—'}</td>
                      <td><Sparkline points={sparklines[h.ticker]} /></td>
                      <td className="mono">{h.market_value != null ? formatMoney(h.market_value) : '—'}</td>
                      <td className={`mono ${h.unrealized_pl >= 0 ? 'gain' : 'loss'}`}>
                        {h.unrealized_pl != null ? formatMoney(h.unrealized_pl) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {analytics && (
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">Analytics</h3>
                {analytics.period_start && (
                  <span className="page-sub mono">
                    {analytics.period_start} → {analytics.period_end}
                    {' '}({analytics.snapshot_count} snapshot{analytics.snapshot_count === 1 ? '' : 's'})
                  </span>
                )}
              </div>

              <div className="analytics-grid">
                <div className="analytics-stat">
                  <span className="stat-label">Total return</span>
                  <span className={`stat-value mono ${
                    analytics.total_return == null ? '' : analytics.total_return >= 0 ? 'gain' : 'loss'
                  }`}>
                    {formatPct(analytics.total_return)}
                  </span>
                </div>
                <div className="analytics-stat">
                  <span className="stat-label">Sharpe</span>
                  <span className="stat-value mono">{formatNum(analytics.sharpe_ratio)}</span>
                </div>
                <div className="analytics-stat">
                  <span className="stat-label">Volatility (ann.)</span>
                  <span className="stat-value mono">{formatPct(analytics.volatility_annualized)}</span>
                </div>
                <div className="analytics-stat">
                  <span className="stat-label">Max drawdown</span>
                  <span className={`stat-value mono ${analytics.max_drawdown ? 'loss' : ''}`}>
                    {formatPct(analytics.max_drawdown?.max_drawdown)}
                  </span>
                </div>
                <div className="analytics-stat">
                  <span className="stat-label">Win rate</span>
                  <span className="stat-value mono">{formatPct(analytics.trades?.win_rate)}</span>
                </div>
                <div className="analytics-stat">
                  <span className="stat-label">Realized P&amp;L</span>
                  <span className={`stat-value mono ${
                    !analytics.trades?.total_realized_pl ? '' :
                      analytics.trades.total_realized_pl >= 0 ? 'gain' : 'loss'
                  }`}>
                    {formatMoney(analytics.trades?.total_realized_pl)}
                  </span>
                </div>
              </div>

              {analytics.trades?.closed_trades > 0 && (
                <p className="page-sub analytics-trades">
                  {analytics.trades.closed_trades} closed trade
                  {analytics.trades.closed_trades === 1 ? '' : 's'} ·{' '}
                  {analytics.trades.wins}W / {analytics.trades.losses}L · best{' '}
                  {formatMoney(analytics.trades.best_trade)}, worst{' '}
                  {formatMoney(analytics.trades.worst_trade)}
                </p>
              )}

              {analytics.sector_exposure?.length > 0 && (
                <div className="sector-list">
                  <span className="stat-label">Sector exposure</span>
                  {analytics.sector_exposure.map((s) => (
                    <div className="sector-row" key={s.sector}>
                      <span className="sector-name">{s.sector}</span>
                      <div className="sector-bar">
                        <div className="sector-fill" style={{ width: `${(s.weight * 100).toFixed(1)}%` }} />
                      </div>
                      <span className="sector-weight mono">{formatPct(s.weight)}</span>
                    </div>
                  ))}
                </div>
              )}

              {}
              {analytics.note && <p className="page-sub analytics-note">{analytics.note}</p>}
            </div>
          )}

          {transactions.length > 0 && (
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">Trade history</h3>
                <span className="badge">{transactions.length} trades</span>
              </div>
              <div className="tx-list">
                {transactions.map((t) => (
                  <div key={t.id} className="tx-row">
                    <div className="tx-row-main">
                      <div className="ticker-chip">{t.ticker.slice(0, 2)}</div>
                      <div className="tx-info">
                        <span className={`tx-action ${t.action}`}>
                          {t.action.toUpperCase()} {t.ticker}
                        </span>
                        <span className="tx-meta mono">
                          {t.shares} sh @ {formatMoney(t.price_per_share)}
                          {t.triggered_by_prediction && (
                            <span className="badge badge-pending" style={{ marginLeft: 6 }}>AI signal</span>
                          )}
                        </span>
                        {t.explanation && (
                          <span className="tx-explain">{t.explanation}</span>
                        )}
                      </div>
                    </div>
                    <div className="tx-row-right">
                      <span className={`mono tx-amount ${t.action === 'buy' ? 'loss' : 'gain'}`}>
                        {t.action === 'buy' ? '-' : '+'}{formatMoney(t.amount)}
                      </span>
                      {t.realized_pl != null && (
                        <span className={`mono tx-pl ${t.realized_pl >= 0 ? 'gain' : 'loss'}`}>
                          P&L {formatMoney(t.realized_pl)}
                        </span>
                      )}
                      <span className="tx-date mono">
                        {new Date(t.executed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="port-col-side">
          <div className="balance-card">
            <div className="balance-card-head">
              <span>Paper portfolio</span>
              <div className="balance-card-plus"><Plus size={14} /></div>
            </div>
            <span className="balance-card-label">Total value</span>
            <span className="balance-card-value">{portfolio ? formatMoney(portfolio.total_value) : '—'}</span>
            <div className="balance-card-footer mono">
              <span>{portfolio ? formatMoney(portfolio.cash_balance, { maximumFractionDigits: 0 }) + ' cash' : ''}</span>
              <span>{portfolio ? formatMoney(portfolio.holdings_value, { maximumFractionDigits: 0 }) + ' holdings' : ''}</span>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Allocation</h3>
            </div>
            {allocation.length ? (
              <div className="donut-wrap">
                <ResponsiveContainer width={120} height={120}>
                  <PieChart>
                    <Pie data={allocation} dataKey="value" innerRadius={38} outerRadius={58} paddingAngle={3} stroke="none">
                      {allocation.map((entry, i) => (
                        <Cell key={entry.name} fill={ALLOCATION_COLORS[i % ALLOCATION_COLORS.length]} />
                      ))}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
                <div className="donut-legend">
                  {allocation.map((a, i) => (
                    <div className="donut-legend-row" key={a.name}>
                      <span className="donut-dot" style={{ background: ALLOCATION_COLORS[i % ALLOCATION_COLORS.length] }} />
                      <span className="mono">{a.name}</span>
                      <span className="donut-legend-value mono">{formatMoney(a.value, { maximumFractionDigits: 0 })}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="empty-state">No holdings yet.</p>
            )}
          </div>

          <form className="card trade-panel" onSubmit={(e) => e.preventDefault()}>
            <div className="card-head">
              <h3 className="card-title">Trade</h3>
              <a className="card-link" href="/">Dashboard <ArrowUpRight size={13} /></a>
            </div>
            <div className="trade-row">
              <input
                className="mono"
                placeholder="Ticker"
                value={tradeTicker}
                onChange={(e) => setTradeTicker(e.target.value.toUpperCase())}
                maxLength={6}
              />
              <input
                className="mono"
                type="number"
                placeholder="Shares"
                value={tradeShares}
                onChange={(e) => setTradeShares(e.target.value)}
                min="0"
                step="any"
              />
            </div>
            <div className="trade-actions">
              <button className="btn btn-primary" disabled={tradeBusy} onClick={() => handleTrade('buy')}>Buy</button>
              <button className="btn btn-ghost" disabled={tradeBusy} onClick={() => handleTrade('sell')}>Sell</button>
            </div>
            {tradeError && <p className="form-error">{tradeError}</p>}
          </form>
        </div>
      </div>
    </Layout>
  );
}
