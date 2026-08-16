// Home.jsx — Landing page and watchlist.
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  PieChart, Pie, Cell, LineChart, Line, ResponsiveContainer,
} from 'recharts';
import {
  ArrowLeftRight, Zap, Star, Route as RouteIcon, ArrowUpRight, Star as StarIcon, X,
} from 'lucide-react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { api, getUserEmail } from '../api/client';
import './Home.css';

const AUTO_INVEST_KEY = 'filium_auto_invest';

function formatMoney(n, opts = {}) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2, ...opts });
}

function timeAgo(dateStr) {
  const diffMs = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`;
  const days = Math.floor(hrs / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

function fakeAcctDigits(email) {
  let h = 0;
  for (const c of email || 'paper') h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return String(h).slice(-4).padStart(4, '0');
}

export default function Home() {
  const navigate = useNavigate();
  const [portfolio, setPortfolio] = useState(null);
  const [history, setHistory] = useState([]);
  const [transactions, setTransactions] = useState([]);
  const [watchlist, setWatchlist] = useState([]);
  const [watchInput, setWatchInput] = useState('');
  const [watchBusy, setWatchBusy] = useState(false);
  const [watchError, setWatchError] = useState('');
  const [statRange, setStatRange] = useState('week');
  const [autoInvest, setAutoInvest] = useState(false);
  const [tradeOpen, setTradeOpen] = useState(false);
  const [tradeTicker, setTradeTicker] = useState('');
  const [tradeShares, setTradeShares] = useState('1');
  const [tradeAction, setTradeAction] = useState('buy');
  const [tradeError, setTradeError] = useState('');
  const [tradeBusy, setTradeBusy] = useState(false);
  const [tradeDone, setTradeDone] = useState('');

  async function loadAll() {
    const [p, h, t, w] = await Promise.allSettled([
      api.getPortfolio(),
      api.getPortfolioHistory(),
      api.getTransactions(30),
      api.getWatchlist(),
    ]);
    if (p.status === 'fulfilled') setPortfolio(p.value);
    if (h.status === 'fulfilled') setHistory(h.value);
    if (t.status === 'fulfilled') setTransactions(t.value);
    if (w.status === 'fulfilled') setWatchlist(w.value);
  }

  useEffect(() => {
    loadAll();
    const saved = localStorage.getItem(AUTO_INVEST_KEY);
    if (saved) {
      try { setAutoInvest(Boolean(JSON.parse(saved).enabled)); } catch {  }
    }
  }, []);

  const email = getUserEmail();
  const acctDigits = fakeAcctDigits(email);

  function toggleAutoInvest() {
    const saved = localStorage.getItem(AUTO_INVEST_KEY);
    let parsed = { enabled: false, threshold: 75 };
    try { if (saved) parsed = JSON.parse(saved); } catch {  }
    const next = { ...parsed, enabled: !autoInvest };
    setAutoInvest(next.enabled);
    localStorage.setItem(AUTO_INVEST_KEY, JSON.stringify(next));
  }

  const rangeStart = useMemo(() => {
    const now = Date.now();
    if (statRange === 'week') return now - 7 * 24 * 60 * 60 * 1000;
    if (statRange === 'month') return now - 30 * 24 * 60 * 60 * 1000;
    return 0;
  }, [statRange]);

  const rangeTx = useMemo(
    () => transactions.filter((t) => new Date(t.executed_at).getTime() >= rangeStart),
    [transactions, rangeStart],
  );

  const buysTotal = rangeTx.filter((t) => t.action === 'buy').reduce((s, t) => s + t.amount, 0);
  const sellsTotal = rangeTx.filter((t) => t.action === 'sell').reduce((s, t) => s + t.amount, 0);
  const statTotal = buysTotal + sellsTotal;

  const donutData = useMemo(() => {
    if (statTotal <= 0) return [{ name: 'None', value: 1 }];
    return [
      { name: 'Buys', value: buysTotal },
      { name: 'Sells', value: sellsTotal },
    ].filter((d) => d.value > 0);
  }, [buysTotal, sellsTotal, statTotal]);

  const recentTrades = transactions.slice(0, 6);
  const sparkline = history.slice(-14).map((h) => ({ date: h.date, value: h.total_value }));

  async function handleAddWatch(e) {
    e.preventDefault();
    const ticker = watchInput.trim().toUpperCase();
    if (!ticker) return;
    setWatchBusy(true);
    setWatchError('');
    try {
      await api.addToWatchlist(ticker);
      setWatchInput('');
      const w = await api.getWatchlist();
      setWatchlist(w);
    } catch (err) {
      setWatchError(err.message);
    } finally {
      setWatchBusy(false);
    }
  }

  function openTrade(action) {
    setTradeAction(action);
    setTradeError('');
    setTradeDone('');
    setTradeOpen(true);
  }

  async function submitTrade(e) {
    e.preventDefault();
    setTradeError('');
    const shares = parseFloat(tradeShares);
    const ticker = tradeTicker.trim().toUpperCase();
    if (!ticker || !shares || shares <= 0) {
      setTradeError('Enter a ticker and a positive number of shares.');
      return;
    }
    setTradeBusy(true);
    try {
      if (tradeAction === 'buy') await api.buy(ticker, shares);
      else await api.sell(ticker, shares);
      setTradeDone(`${tradeAction === 'buy' ? 'Bought' : 'Sold'} ${shares} sh of ${ticker}.`);
      setTradeTicker('');
      setTradeShares('1');
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

      <div className="home-grid">
        <div className="home-col-main">

          <div className="section-head">
            <h3 className="card-title">Cards</h3>
            <a className="card-link" href="/portfolio" onClick={(e) => { e.preventDefault(); navigate('/portfolio'); }}>
              See all <ArrowUpRight size={13} />
            </a>
          </div>

          <div className="cards-row">
            <div className="paper-card paper-card-dark">
              <div className="paper-card-top">
                <span className="paper-card-balance mono">{portfolio ? formatMoney(portfolio.total_value) : '—'}</span>
                <button className="paper-card-dots">⋮</button>
              </div>
              <span className="paper-card-mask mono">•••• {acctDigits}</span>
              <div className="paper-card-bottom">
                <span className="mono">SINCE {new Date().getFullYear()}</span>
                <span className="paper-card-brand">PAPER</span>
              </div>
            </div>

            <div className="paper-card paper-card-teal">
              <div className="paper-card-top">
                <span className="paper-card-balance mono">{portfolio ? formatMoney(portfolio.cash_balance) : '—'}</span>
                <button
                  className={`switch switch-on-card ${autoInvest ? 'switch-on' : ''}`}
                  onClick={toggleAutoInvest}
                  title="Auto-invest on strong signals"
                  aria-pressed={autoInvest}
                >
                  <span className="switch-knob" />
                </button>
              </div>
              <span className="paper-card-mask mono">Buying power</span>
              <div className="paper-card-bottom">
                <span className="mono">AUTO-INVEST</span>
                <span className="paper-card-brand">{autoInvest ? 'ON' : 'OFF'}</span>
              </div>
            </div>
          </div>

          <div className="quick-actions">
            <button className="qa qa-active" onClick={() => openTrade('buy')}>
              <ArrowLeftRight size={16} />
              <span>Trade</span>
            </button>
            <button className="qa" onClick={() => navigate('/chat')}>
              <div className="qa-circle"><Zap size={16} /></div>
              <span>Ask AI</span>
            </button>
            <button
              className="qa"
              onClick={() => document.getElementById('watchlist-card')?.scrollIntoView({ behavior: 'smooth' })}
            >
              <div className="qa-circle"><StarIcon size={16} /></div>
              <span>Watchlist</span>
            </button>
            <button className="qa" onClick={() => navigate('/dashboard')}>
              <div className="qa-circle"><RouteIcon size={16} /></div>
              <span>Predict</span>
            </button>
          </div>

          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Recent Trades</h3>
              <a className="card-link" href="/portfolio" onClick={(e) => { e.preventDefault(); navigate('/portfolio'); }}>
                View all <ArrowUpRight size={13} />
              </a>
            </div>
            {recentTrades.length ? (
              <table className="sales-table">
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Date</th>
                    <th>Status</th>
                    <th style={{ textAlign: 'right' }}>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {recentTrades.map((t, i) => (
                    <tr key={i}>
                      <td>
                        <div className="sales-name">
                          <div className="ticker-chip">{t.ticker.slice(0, 2)}</div>
                          <div>
                            <div className="sales-name-main">{t.ticker}</div>
                            <div className="sales-name-sub">{t.shares} sh · {t.action}</div>
                          </div>
                        </div>
                      </td>
                      <td className="mono">{new Date(t.executed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</td>
                      <td>
                        <span className={`badge ${t.triggered_by_prediction ? 'badge-pending' : 'badge-gain'}`}>
                          {t.triggered_by_prediction ? 'Automated' : 'Success'}
                        </span>
                      </td>
                      <td className={`mono ${t.action === 'buy' ? 'loss' : 'gain'}`} style={{ textAlign: 'right' }}>
                        {t.action === 'buy' ? '-' : '+'}{formatMoney(t.amount)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="empty-state">No trades yet — use the Trade button above to place one.</p>
            )}
          </div>
        </div>

        <div className="home-col-side">
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Statistic</h3>
              <select value={statRange} onChange={(e) => setStatRange(e.target.value)} className="range-select">
                <option value="week">This week</option>
                <option value="month">This month</option>
                <option value="all">All time</option>
              </select>
            </div>

            <div className="stat-donut-wrap">
              <ResponsiveContainer width={150} height={150}>
                <PieChart>
                  <Pie
                    data={donutData}
                    dataKey="value"
                    innerRadius={52}
                    outerRadius={72}
                    startAngle={90}
                    endAngle={-270}
                    stroke="none"
                  >
                    {statTotal > 0 ? (
                      donutData.map((d) => (
                        <Cell key={d.name} fill={d.name === 'Buys' ? 'var(--color-accent)' : 'var(--color-ink)'} />
                      ))
                    ) : (
                      <Cell fill="var(--color-border)" />
                    )}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="stat-donut-center">
                <span className="stat-donut-label">Total</span>
                <span className="stat-donut-value mono">{formatMoney(statTotal, { maximumFractionDigits: 0 })}</span>
              </div>
            </div>

            <div className="stat-legend">
              <span><i style={{ background: 'var(--color-accent)' }} /> Buys ({formatMoney(buysTotal, { maximumFractionDigits: 0 })})</span>
              <span><i style={{ background: 'var(--color-ink)' }} /> Sells ({formatMoney(sellsTotal, { maximumFractionDigits: 0 })})</span>
            </div>

            <div className="stat-tx-list">
              {rangeTx.slice(0, 5).map((t, i) => (
                <div className="stat-tx-row" key={i}>
                  <div className="ticker-chip">{t.ticker.slice(0, 2)}</div>
                  <div className="stat-tx-info">
                    <span className="stat-tx-ticker">{t.ticker}</span>
                    <span className="stat-tx-meta">{t.action === 'buy' ? 'Trade on the app' : 'Money in'} · {timeAgo(t.executed_at)}</span>
                  </div>
                  <span className={`mono stat-tx-amount ${t.action === 'buy' ? 'loss' : 'gain'}`}>
                    {t.action === 'buy' ? '-' : '+'}{formatMoney(t.amount, { maximumFractionDigits: 0 })}
                  </span>
                </div>
              ))}
              {!rangeTx.length && <p className="empty-state">No activity in this range.</p>}
            </div>
          </div>

          <div className="card watch-card" id="watchlist-card">
            <form onSubmit={handleAddWatch}>
              <div className="card-head">
                <h3 className="card-title">Watchlist</h3>
                <Star size={15} color="var(--color-accent)" fill="var(--color-accent)" />
              </div>
              <p className="page-sub" style={{ margin: '0 0 10px' }}>
                {watchlist.length ? `${watchlist.length} ticker${watchlist.length === 1 ? '' : 's'} tracked` : 'Track a ticker for quick signals'}
              </p>
              <div className="watch-input-row">
                <input
                  className="mono"
                  placeholder="Ticker"
                  value={watchInput}
                  onChange={(e) => setWatchInput(e.target.value.toUpperCase())}
                  maxLength={6}
                />
                <button className="btn btn-primary" disabled={watchBusy} type="submit">Add</button>
              </div>
              {watchError && <p className="form-error">{watchError}</p>}
            </form>
            {watchlist.length > 0 && (
              <div className="watch-chip-row">
                {watchlist.map((w) => (
                  <button key={w.ticker} className="pill" onClick={() => navigate(`/dashboard?ticker=${w.ticker}`)}>
                    {w.ticker}
                  </button>
                ))}
              </div>
            )}
          </div>

          {sparkline.length > 1 && (
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">Portfolio trend</h3>
              </div>
              <ResponsiveContainer width="100%" height={70}>
                <LineChart data={sparkline}>
                  <Line type="monotone" dataKey="value" stroke="var(--color-accent)" dot={false} strokeWidth={2.5} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      {tradeOpen && (
        <div className="modal-overlay" onClick={() => setTradeOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="card-head">
              <h3 className="card-title">Quick trade</h3>
              <button className="modal-close" onClick={() => setTradeOpen(false)}><X size={16} /></button>
            </div>
            <div className="pill-row" style={{ marginBottom: 14 }}>
              <button className={`pill${tradeAction === 'buy' ? ' pill-active' : ''}`} onClick={() => setTradeAction('buy')}>Buy</button>
              <button className={`pill${tradeAction === 'sell' ? ' pill-active' : ''}`} onClick={() => setTradeAction('sell')}>Sell</button>
            </div>
            <form onSubmit={submitTrade}>
              <div className="trade-row">
                <input
                  className="mono"
                  placeholder="Ticker"
                  value={tradeTicker}
                  onChange={(e) => setTradeTicker(e.target.value.toUpperCase())}
                  maxLength={6}
                  autoFocus
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
              <button className="btn btn-primary" style={{ width: '100%', marginTop: 12 }} disabled={tradeBusy} type="submit">
                {tradeBusy ? 'Working…' : tradeAction === 'buy' ? 'Buy shares' : 'Sell shares'}
              </button>
              {tradeError && <p className="form-error">{tradeError}</p>}
              {tradeDone && <p className="page-sub" style={{ marginTop: 8 }}>{tradeDone}</p>}
            </form>
          </div>
        </div>
      )}
    </Layout>
  );
}
