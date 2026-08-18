// Home.jsx — FundFlow Finance & Research Dashboard
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ResponsiveContainer, BarChart, Bar, XAxis, Tooltip, AreaChart, Area,
} from 'recharts';
import {
  ArrowUpRight, Plus, ChevronRight, ChevronDown, RotateCcw,
  Sparkles, TrendingUp, TrendingDown, ArrowRight, X, Info
} from 'lucide-react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { api, getUserEmail } from '../api/client';
import './Home.css';

const AUTO_INVEST_KEY = 'filium_auto_invest';

function formatMoney(n, opts = {}) {
  if (n == null) return '$0.00';
  return n.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    ...opts
  });
}

function formatShortMoney(n) {
  if (n == null) return '$0';
  if (Math.abs(n) >= 1000) {
    return `$${(n / 1000).toFixed(0)}k`;
  }
  return `$${n.toFixed(0)}`;
}

// Monthly expense/trading statistic dataset matching FundFlow chart design
const EXPENSE_STATS_MONTHLY = [
  { month: 'MAY', value: 24, label: '$24k' },
  { month: 'JUN', value: 31, label: '$31k' },
  { month: 'JUL', value: 45, label: '$45k', active: true },
  { month: 'AUG', value: 28, label: '$28k' },
  { month: 'SEP', value: 36, label: '$36k' },
];

const EXPENSE_STATS_WEEKLY = [
  { month: 'MON', value: 8, label: '$8k' },
  { month: 'TUE', value: 14, label: '$14k' },
  { month: 'WED', value: 25, label: '$25k', active: true },
  { month: 'THU', value: 12, label: '$12k' },
  { month: 'FRI', value: 18, label: '$18k' },
];

// Financial health sparkline points matching the glowing cyan card
const HEALTH_SPARKLINE = [
  { step: 1, val: 7.26 },
  { step: 2, val: 6.80 },
  { step: 3, val: 8.40 },
  { step: 4, val: 7.10 },
  { step: 5, val: 9.30 },
  { step: 6, val: 8.60 },
  { step: 7, val: 10.75 },
];

// Default sample upcoming payments
const DEFAULT_UPCOMING = [
  {
    id: 1,
    name: 'Stripe Pricing',
    category: 'Payment Links',
    dateBadge: 'Today',
    isToday: true,
    amount: 1200.00,
    logoBg: '#635BFF',
    symbol: 'S'
  },
  {
    id: 2,
    name: 'FigJam Membership',
    category: 'Professional',
    dateBadge: 'Jun 23',
    isToday: false,
    amount: 155.00,
    logoBg: '#0ACF83',
    symbol: 'F'
  },
  {
    id: 3,
    name: 'Loom Subscription',
    category: 'Loom Business',
    dateBadge: 'Jul 15',
    isToday: false,
    amount: 100.00,
    logoBg: '#625DF5',
    symbol: 'L'
  },
];

// Default sample transactions (matching FundFlow right panel)
const DEFAULT_TRANSACTIONS = [
  { name: 'YouTube', date: 'Jun 15', status: 'Pending', amount: -50.00 },
  { name: 'John Doe', date: 'Jun 14', status: 'Done', amount: -100.00 },
  { name: 'Sans Brothers', date: 'Jun 13', status: 'Done', amount: 120.00 },
  { name: 'John Doe', date: 'Jun 8', status: 'Done', amount: -100.00 },
  { name: 'Cinema City', date: 'Jun 6', status: 'Done', amount: -75.00 },
  { name: 'To USD', date: 'Jun 1', status: 'Done', amount: -250.00 },
];

// Contacts for Quick Transfer
const CONTACTS = [
  { name: 'F. Alonso', avatar: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=100&auto=format&fit=crop&q=80' },
  { name: 'C. Leclerc', avatar: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=100&auto=format&fit=crop&q=80' },
  { name: 'M. Naira', avatar: 'https://images.unsplash.com/photo-1517841905240-472988babdf9?w=100&auto=format&fit=crop&q=80' },
];

export default function Home() {
  const navigate = useNavigate();
  const [portfolio, setPortfolio] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [watchlist, setWatchlist] = useState([]);
  const [currency, setCurrency] = useState('USD');
  const [statPeriod, setStatPeriod] = useState('Monthly');
  const [selectedContact, setSelectedContact] = useState(0);
  const [transferAmount, setTransferAmount] = useState('100.00');
  const [transferDone, setTransferDone] = useState(false);
  const [tipsModalOpen, setTipsModalOpen] = useState(false);

  // Trade Modal State
  const [tradeOpen, setTradeOpen] = useState(false);
  const [tradeAction, setTradeAction] = useState('buy');
  const [tradeTicker, setTradeTicker] = useState('AAPL');
  const [tradeShares, setTradeShares] = useState('5');
  const [tradeBusy, setTradeBusy] = useState(false);
  const [tradeError, setTradeError] = useState('');
  const [tradeSuccess, setTradeSuccess] = useState('');

  // Deposit Modal State
  const [depositOpen, setDepositOpen] = useState(false);
  const [depositAmount, setDepositAmount] = useState('10000');
  const [depositBusy, setDepositBusy] = useState(false);
  const [depositSuccess, setDepositSuccess] = useState('');

  async function loadData() {
    try {
      const [p, t, w] = await Promise.allSettled([
        api.getPortfolio(),
        api.getTransactions(15),
        api.getWatchlist(),
      ]);
      if (p.status === 'fulfilled') setPortfolio(p.value);
      if (t.status === 'fulfilled') setTransactions(t.value);
      if (w.status === 'fulfilled') setWatchlist(w.value);
    } catch {
      // Fallbacks keep the UI resilient
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  // Compute balance values
  const totalBalance = portfolio?.total_value ?? 73558.00;
  const cashBalance = portfolio?.cash_balance ?? 10208.00;
  const holdingsValue = (portfolio?.total_value && portfolio?.cash_balance)
    ? Math.max(0, portfolio.total_value - portfolio.cash_balance)
    : 39792.00;
  const mastercardBalance = (totalBalance > 0 && cashBalance > 0)
    ? Math.min(totalBalance, 23558.00)
    : 23558.00;

  // Currency multiplier
  const curMult = currency === 'EUR' ? 0.92 : 1.0;
  const curSymbol = currency === 'EUR' ? '€' : '$';

  // Merge live transactions with FundFlow layout
  const displayTransactions = useMemo(() => {
    if (transactions.length > 0) {
      return transactions.slice(0, 6).map((t) => ({
        name: `${t.ticker} (${t.shares} sh)`,
        date: new Date(t.executed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
        status: t.triggered_by_prediction ? 'Pending' : 'Done',
        amount: t.action === 'buy' ? -t.amount : t.amount,
      }));
    }
    return DEFAULT_TRANSACTIONS;
  }, [transactions]);

  // Handle Quick Transfer
  async function handleQuickTransfer(e) {
    e.preventDefault();
    const amountNum = parseFloat(transferAmount);
    if (!amountNum || amountNum <= 0) return;
    
    // Simulate sending / paper money transfer
    setTransferDone(true);
    setTimeout(() => setTransferDone(false), 3000);
  }

  // Handle Quick Trade
  async function submitTrade(e) {
    e.preventDefault();
    setTradeError('');
    setTradeSuccess('');
    const ticker = tradeTicker.trim().toUpperCase();
    const shares = parseFloat(tradeShares);
    if (!ticker || !shares || shares <= 0) {
      setTradeError('Please enter a valid ticker and share quantity.');
      return;
    }
    setTradeBusy(true);
    try {
      if (tradeAction === 'buy') await api.buy(ticker, shares);
      else await api.sell(ticker, shares);
      setTradeSuccess(`Successfully ${tradeAction === 'buy' ? 'bought' : 'sold'} ${shares} shares of ${ticker}!`);
      await loadData();
      setTimeout(() => {
        setTradeOpen(false);
        setTradeSuccess('');
      }, 1400);
    } catch (err) {
      setTradeError(err.message || 'Trade execution failed.');
    } finally {
      setTradeBusy(false);
    }
  }

  // Handle Cash Deposit
  async function submitDeposit(e) {
    e.preventDefault();
    const amount = parseFloat(depositAmount);
    if (!amount || amount <= 0) return;
    setDepositBusy(true);
    try {
      await api.deposit(amount);
      setDepositSuccess(`Added ${formatMoney(amount)} to your paper trading cash!`);
      await loadData();
      setTimeout(() => {
        setDepositOpen(false);
        setDepositSuccess('');
      }, 1400);
    } catch (err) {
      alert(err.message || 'Deposit failed.');
    } finally {
      setDepositBusy(false);
    }
  }

  const expenseData = statPeriod === 'Monthly' ? EXPENSE_STATS_MONTHLY : EXPENSE_STATS_WEEKLY;

  return (
    <Layout>
      <Topbar onSearchTrigger={(t) => navigate(`/dashboard?ticker=${t}`)} />

      <div className="fundflow-dashboard-grid">
        {/* CENTER COLUMN: Main Content Cards */}
        <div className="fundflow-center-column">

          {/* CARD 1: Total Balance Card with Overlapping Account Bubbles */}
          <div className="fundflow-balance-card">
            <div className="fundflow-balance-header">
              <span className="fundflow-balance-label">Total balance</span>
              <div className="fundflow-currency-toggle">
                <button
                  className={`fundflow-currency-btn ${currency === 'EUR' ? 'active' : ''}`}
                  onClick={() => setCurrency('EUR')}
                >
                  EUR
                </button>
                <button
                  className={`fundflow-currency-btn ${currency === 'USD' ? 'active' : ''}`}
                  onClick={() => setCurrency('USD')}
                >
                  USD
                </button>
              </div>
            </div>

            <div className="fundflow-balance-figure">
              {curSymbol}{(totalBalance * curMult).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>

            {/* 3 Overlapping Account Bubbles */}
            <div className="fundflow-bubbles-wrapper">
              <div className="fundflow-bubble fundflow-bubble-side fundflow-bubble-left">
                <span className="bubble-amount">{formatMoney(cashBalance * curMult, { maximumFractionDigits: 0 })}</span>
                <span className="bubble-brand">Cash / Visa</span>
              </div>

              <div className="fundflow-bubble fundflow-bubble-center">
                <span className="bubble-amount">{formatMoney(mastercardBalance * curMult, { maximumFractionDigits: 0 })}</span>
                <span className="bubble-brand">Mastercard</span>
              </div>

              <div className="fundflow-bubble fundflow-bubble-side fundflow-bubble-right">
                <span className="bubble-amount">{formatMoney(holdingsValue * curMult, { maximumFractionDigits: 0 })}</span>
                <span className="bubble-brand">Savings</span>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="fundflow-balance-actions">
              <button
                className="btn btn-outline fundflow-btn-pill"
                onClick={() => setDepositOpen(true)}
              >
                Receive Money
              </button>
              <button
                className="btn btn-black fundflow-btn-pill"
                onClick={() => {
                  setTradeAction('buy');
                  setTradeOpen(true);
                }}
              >
                Send Money
              </button>
            </div>
          </div>

          {/* ROW OF 2 CARDS: Expense Statistic + Financial Health */}
          <div className="fundflow-stats-row">
            {/* CARD 2: Expense Statistic Bar Chart */}
            <div className="card fundflow-expense-card">
              <div className="card-head">
                <span className="card-title">Expense statistic</span>
                <div className="fundflow-dropdown-pill">
                  <select
                    value={statPeriod}
                    onChange={(e) => setStatPeriod(e.target.value)}
                    className="fundflow-select-raw"
                  >
                    <option value="Monthly">Monthly</option>
                    <option value="Weekly">Weekly</option>
                  </select>
                  <ChevronDown size={14} className="dropdown-arrow" />
                </div>
              </div>

              <div className="fundflow-barchart-container">
                <div className="custom-bar-chart">
                  {expenseData.map((item) => (
                    <div key={item.month} className={`bar-col ${item.active ? 'bar-active' : ''}`}>
                      {item.active && (
                        <div className="bar-tooltip-pill">
                          {item.label}
                        </div>
                      )}
                      <div className="bar-track">
                        <div
                          className={`bar-fill ${item.active ? 'bar-fill-gradient' : ''}`}
                          style={{ height: `${(item.value / 45) * 100}%` }}
                        />
                      </div>
                      <span className="bar-label">{item.month}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* CARD 3: Financial Health Glowing Cyan Card */}
            <div className="fundflow-health-card">
              <div className="health-card-head">
                <span className="health-card-title">Financial health</span>
                <button
                  className="health-expand-btn"
                  onClick={() => navigate('/leaderboard')}
                  title="View Model Leaderboard & Health"
                >
                  <RotateCcw size={14} strokeWidth={2.4} />
                </button>
              </div>

              <div className="health-stat-block">
                <div className="health-stat-value">85%</div>
                <span className="health-stat-sub">since last month</span>
              </div>

              <div className="health-sparkline-wrap">
                <ResponsiveContainer width="100%" height={68}>
                  <AreaChart data={HEALTH_SPARKLINE} margin={{ top: 10, right: 8, left: 8, bottom: 0 }}>
                    <defs>
                      <linearGradient id="healthGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#ffffff" stopOpacity={0.4} />
                        <stop offset="100%" stopColor="#ffffff" stopOpacity={0.0} />
                      </linearGradient>
                    </defs>
                    <Area
                      type="monotone"
                      dataKey="val"
                      stroke="#ffffff"
                      strokeWidth={2.5}
                      fill="url(#healthGrad)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
                <div className="health-axis-labels">
                  <span>7.26k</span>
                  <div className="sparkline-active-dot" />
                  <span>10.75k</span>
                </div>
              </div>
            </div>
          </div>

          {/* CARD 4: Upcoming Payments / Active Watchlist */}
          <div className="card fundflow-upcoming-card">
            <div className="card-head">
              <span className="card-title">Upcoming payments</span>
              <button
                className="btn btn-pill-viewall"
                onClick={() => navigate('/portfolio')}
              >
                View All
              </button>
            </div>

            <div className="fundflow-payments-list">
              {DEFAULT_UPCOMING.map((item, idx) => (
                <div key={item.id} className="payment-row">
                  <div className="payment-left">
                    <div className="payment-logo-tile" style={{ background: item.logoBg }}>
                      {item.symbol}
                    </div>
                    <div className="payment-info">
                      <span className="payment-name">{item.name}</span>
                      <span className="payment-category">{item.category}</span>
                    </div>
                  </div>

                  <div className="payment-center">
                    <span className={item.isToday ? 'badge-pending' : 'badge-done'}>
                      {item.dateBadge}
                    </span>
                  </div>

                  <div className="payment-right">
                    <span className="payment-amount">${item.amount.toLocaleString('en-US', { minimumFractionDigits: 0 })}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN: Transactions Panel + Tip Banner + Quick Transfer */}
        <div className="fundflow-right-column">

          {/* SECTION: Transactions List */}
          <div className="fundflow-transactions-panel">
            <div className="transactions-list">
              {displayTransactions.map((tx, idx) => (
                <div key={idx} className="tx-item-row">
                  <div className="tx-left">
                    <div className="tx-bullet" />
                    <div className="tx-details">
                      <span className="tx-name">{tx.name}</span>
                      <span className="tx-date">{tx.date}</span>
                    </div>
                  </div>

                  <div className="tx-center">
                    <span className={tx.status === 'Pending' ? 'badge-pending' : 'badge-done'}>
                      {tx.status}
                    </span>
                  </div>

                  <div className="tx-right">
                    <span className={`tx-amount ${tx.amount < 0 ? 'tx-loss' : 'tx-gain'}`}>
                      {tx.amount < 0 ? '-' : '+'}${Math.abs(tx.amount).toFixed(0)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* SECTION: Tip Banner */}
          <div className="fundflow-tip-banner">
            <h4 className="tip-banner-title">How to reduce expenses by 25%?</h4>
            <p className="tip-banner-sub">View these useful tips to save your money.</p>
            <button
              className="tip-banner-link"
              onClick={() => setTipsModalOpen(true)}
            >
              Learn more <ArrowRight size={13} />
            </button>
          </div>

          {/* CARD: Quick Transfer */}
          <div className="card fundflow-quick-transfer-card">
            <div className="card-head">
              <span className="card-title">Quick transfer</span>
              <div className="quick-transfer-links">
                <button className="qt-link active">All</button>
                <button className="qt-link" onClick={() => navigate('/portfolio')}>Contacts</button>
              </div>
            </div>

            {/* Avatars Row */}
            <div className="quick-transfer-avatars-row">
              <button
                className="add-new-avatar-btn"
                onClick={() => setTradeOpen(true)}
                title="Add new contact / trade"
              >
                <div className="dashed-plus-circle">
                  <Plus size={16} />
                </div>
                <span>Add new</span>
              </button>

              {CONTACTS.map((c, i) => (
                <div
                  key={c.name}
                  className={`contact-avatar-col ${selectedContact === i ? 'selected' : ''}`}
                  onClick={() => setSelectedContact(i)}
                >
                  <img src={c.avatar} alt={c.name} className="contact-avatar-img" />
                  <span className="contact-name">{c.name}</span>
                </div>
              ))}

              <button
                className="qt-chevron-btn"
                onClick={() => setSelectedContact((prev) => (prev + 1) % CONTACTS.length)}
              >
                <ChevronRight size={18} />
              </button>
            </div>

            {/* Amount Input & Send Button */}
            <form className="quick-transfer-action-row" onSubmit={handleQuickTransfer}>
              <div className="qt-amount-input-wrap">
                <span className="qt-dollar-sign">$</span>
                <input
                  type="text"
                  value={transferAmount}
                  onChange={(e) => setTransferAmount(e.target.value)}
                  className="qt-amount-input"
                />
              </div>
              <button type="submit" className="btn btn-black qt-send-btn">
                {transferDone ? 'Sent ✓' : 'Send'}
              </button>
            </form>
          </div>
        </div>
      </div>

      {/* QUICK TRADE MODAL */}
      {tradeOpen && (
        <div className="modal-overlay" onClick={() => setTradeOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="card-head">
              <h3 className="card-title">Place Order</h3>
              <button className="modal-close" onClick={() => setTradeOpen(false)}>
                <X size={16} />
              </button>
            </div>

            <div className="pill-row" style={{ marginBottom: 16 }}>
              <button
                type="button"
                className={`pill ${tradeAction === 'buy' ? 'pill-active' : ''}`}
                onClick={() => setTradeAction('buy')}
              >
                Buy
              </button>
              <button
                type="button"
                className={`pill ${tradeAction === 'sell' ? 'pill-active' : ''}`}
                onClick={() => setTradeAction('sell')}
              >
                Sell
              </button>
            </div>

            <form onSubmit={submitTrade}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                    Ticker
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. AAPL"
                    value={tradeTicker}
                    onChange={(e) => setTradeTicker(e.target.value.toUpperCase())}
                    style={{ width: '100%' }}
                    autoFocus
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                    Shares
                  </label>
                  <input
                    type="number"
                    step="any"
                    min="0.01"
                    placeholder="Shares"
                    value={tradeShares}
                    onChange={(e) => setTradeShares(e.target.value)}
                    style={{ width: '100%' }}
                  />
                </div>
              </div>

              {tradeError && <p className="form-error">{tradeError}</p>}
              {tradeSuccess && <p className="gain" style={{ fontSize: 13, marginTop: 8 }}>{tradeSuccess}</p>}

              <button
                type="submit"
                className="btn btn-black"
                style={{ width: '100%', marginTop: 20 }}
                disabled={tradeBusy}
              >
                {tradeBusy ? 'Executing…' : `${tradeAction === 'buy' ? 'Buy' : 'Sell'} ${tradeTicker}`}
              </button>
            </form>
          </div>
        </div>
      )}

      {/* RECEIVE / DEPOSIT MODAL */}
      {depositOpen && (
        <div className="modal-overlay" onClick={() => setDepositOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="card-head">
              <h3 className="card-title">Deposit Virtual Cash</h3>
              <button className="modal-close" onClick={() => setDepositOpen(false)}>
                <X size={16} />
              </button>
            </div>
            <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: '0 0 16px' }}>
              Add simulated funds to your paper trading ledger balance.
            </p>
            <form onSubmit={submitDeposit}>
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 6 }}>
                  Deposit Amount ($)
                </label>
                <input
                  type="number"
                  step="100"
                  min="1"
                  value={depositAmount}
                  onChange={(e) => setDepositAmount(e.target.value)}
                  style={{ width: '100%', fontSize: 18, fontWeight: 700 }}
                  autoFocus
                />
              </div>

              {depositSuccess && <p className="gain" style={{ fontSize: 13, marginBottom: 12 }}>{depositSuccess}</p>}

              <button
                type="submit"
                className="btn btn-black"
                style={{ width: '100%' }}
                disabled={depositBusy}
              >
                {depositBusy ? 'Processing…' : 'Deposit Funds'}
              </button>
            </form>
          </div>
        </div>
      )}

      {/* TIPS MODAL */}
      {tipsModalOpen && (
        <div className="modal-overlay" onClick={() => setTipsModalOpen(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="card-head">
              <h3 className="card-title">Quantitative Trading & Research Tips</h3>
              <button className="modal-close" onClick={() => setTipsModalOpen(false)}>
                <X size={16} />
              </button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 10 }}>
              <div style={{ padding: 12, background: 'var(--color-surface-sunken)', borderRadius: 12 }}>
                <strong style={{ fontSize: 13, display: 'block', color: 'var(--color-ink)' }}>1. High Confidence Filtering</strong>
                <span style={{ fontSize: 12.5, color: 'var(--color-text-muted)' }}>
                  Only execute trades when model confidence is $\ge 65\%$. Test this in the Strategy Sandbox!
                </span>
              </div>
              <div style={{ padding: 12, background: 'var(--color-surface-sunken)', borderRadius: 12 }}>
                <strong style={{ fontSize: 13, display: 'block', color: 'var(--color-ink)' }}>2. Cross-check SEC Risk Factors</strong>
                <span style={{ fontSize: 12.5, color: 'var(--color-text-muted)' }}>
                  Use the Filings RAG Assistant to verify supply chain and debt risks before buying.
                </span>
              </div>
              <div style={{ padding: 12, background: 'var(--color-surface-sunken)', borderRadius: 12 }}>
                <strong style={{ fontSize: 13, display: 'block', color: 'var(--color-ink)' }}>3. FinBERT Sentiment Confirmation</strong>
                <span style={{ fontSize: 12.5, color: 'var(--color-text-muted)' }}>
                  Confirm technical LSTM buy signals with positive news sentiment polarity on the Company Dashboard.
                </span>
              </div>
            </div>
            <button
              className="btn btn-black"
              style={{ width: '100%', marginTop: 20 }}
              onClick={() => setTipsModalOpen(false)}
            >
              Got it
            </button>
          </div>
        </div>
      )}
    </Layout>
  );
}
