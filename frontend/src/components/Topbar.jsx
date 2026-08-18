// Topbar.jsx — FundFlow Top Navigation Bar
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Bell, TrendingUp, TrendingDown, CreditCard } from 'lucide-react';
import { api, getUserEmail } from '../api/client';

function fakeCardExpiry(email) {
  let h = 0;
  for (const c of email || 'user') h = (h * 37 + c.charCodeAt(0)) >>> 0;
  const month = String((h % 12) + 1).padStart(2, '0');
  const year = String(28 + (h % 5));
  return `${month}/${year}`;
}

function fakeAcctDigits(email) {
  let h = 0;
  for (const c of email || 'user') h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return String(h).slice(-4).padStart(4, '0');
}

export default function Topbar({ onSearchTrigger }) {
  const [q, setQ] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const [signals, setSignals] = useState([]);
  const [signalsLoaded, setSignalsLoaded] = useState(false);
  const navigate = useNavigate();
  const bellRef = useRef(null);
  const searchRef = useRef(null);

  const email = getUserEmail();
  const cardDigits = fakeAcctDigits(email);
  const cardExpiry = fakeCardExpiry(email);

  useEffect(() => {
    function onClick(e) {
      if (bellRef.current && !bellRef.current.contains(e.target)) setBellOpen(false);
      if (searchRef.current && !searchRef.current.contains(e.target)) setSearchOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  async function loadSignals() {
    if (signalsLoaded) return;
    try {
      const watchlist = await api.getWatchlist();
      const results = await Promise.allSettled(
        watchlist.map((w) => api.getPrediction(w.ticker)),
      );
      const rows = results
        .map((r, i) => (r.status === 'fulfilled' ? { ...r.value, ticker: watchlist[i].ticker } : null))
        .filter(Boolean)
        .sort((a, b) => b.confidence - a.confidence);
      setSignals(rows);
    } catch {
      setSignals([]);
    } finally {
      setSignalsLoaded(true);
    }
  }

  function handleSearch(e) {
    e.preventDefault();
    const ticker = q.trim().toUpperCase();
    if (!ticker) return;
    if (onSearchTrigger) onSearchTrigger(ticker);
    else navigate(`/dashboard?ticker=${ticker}`);
    setQ('');
    setSearchOpen(false);
  }

  return (
    <header className="topbar">
      <div className="topbar-brand-block">
        <h1 className="topbar-brand-title">FundFlow</h1>
        <span className="topbar-brand-sub">Start managing your finances</span>
      </div>

      <div className="topbar-center-card-pill" title="Virtual Paper Trading Mastercard">
        <span>•••• {cardDigits}</span>
        <span>{cardExpiry}</span>
      </div>

      <div className="topbar-right-block">
        <div className="topbar-tx-heading">
          <strong>Transactions</strong>
          <span>Latest transfers</span>
        </div>

        <div style={{ position: 'relative' }} ref={searchRef}>
          <button
            className="topbar-circle-btn"
            onClick={() => setSearchOpen((v) => !v)}
            title="Search Ticker"
          >
            <Search size={18} strokeWidth={2.2} />
          </button>

          {searchOpen && (
            <div className="topbar-dropdown" style={{ right: 0, minWidth: 260 }}>
              <p className="topbar-dropdown-title">Search Market</p>
              <form onSubmit={handleSearch} style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <input
                  type="text"
                  placeholder="e.g. AAPL, MSFT, NVDA"
                  value={q}
                  onChange={(e) => setQ(e.target.value.toUpperCase())}
                  autoFocus
                  style={{ flex: 1, padding: '6px 12px', fontSize: 13 }}
                />
                <button type="submit" className="btn btn-black" style={{ padding: '6px 14px', fontSize: 12 }}>
                  Go
                </button>
              </form>
            </div>
          )}
        </div>

        <div style={{ position: 'relative' }} ref={bellRef}>
          <button
            className="topbar-circle-btn"
            onClick={() => {
              setBellOpen((v) => !v);
              loadSignals();
            }}
            title="AI Signal Alerts"
          >
            <Bell size={18} strokeWidth={2.2} />
            {signals.length > 0 && <span className="topbar-bell-dot" />}
          </button>

          {bellOpen && (
            <div className="topbar-dropdown" onClick={(e) => e.stopPropagation()}>
              <p className="topbar-dropdown-title">Signal Alerts</p>
              <p className="topbar-dropdown-sub">Live forecasts for your watchlist</p>
              {!signalsLoaded ? (
                <p className="empty-state" style={{ padding: '8px 0' }}>Loading signals…</p>
              ) : signals.length ? (
                <div className="notif-list">
                  {signals.map((s) => (
                    <a
                      key={s.ticker}
                      className="notif-row"
                      href={`/dashboard?ticker=${s.ticker}`}
                      onClick={(e) => {
                        e.preventDefault();
                        navigate(`/dashboard?ticker=${s.ticker}`);
                        setBellOpen(false);
                      }}
                    >
                      <div className="ticker-chip" style={{ width: 28, height: 28, fontSize: 10 }}>
                        {s.ticker.slice(0, 2)}
                      </div>
                      <div className="notif-text">
                        <strong>
                          {s.ticker} predicted {s.predicted_direction === 'up' ? 'UP' : 'DOWN'}
                          {' '}{s.predicted_direction === 'up'
                            ? <TrendingUp size={12} style={{ verticalAlign: -1, color: 'var(--color-gain)' }} />
                            : <TrendingDown size={12} style={{ verticalAlign: -1, color: 'var(--color-loss)' }} />}
                        </strong>
                        <span>{(s.confidence * 100).toFixed(0)}% confidence · target {s.target_date}</span>
                      </div>
                    </a>
                  ))}
                </div>
              ) : (
                <p className="empty-state" style={{ padding: '8px 0' }}>
                  No active signals — add a ticker to watchlist to get AI forecasts.
                </p>
              )}
            </div>
          )}
        </div>

        <button
          className="btn btn-pill-viewall"
          onClick={() => navigate('/portfolio')}
          title="View all transactions and portfolio ledger"
        >
          View All
        </button>
      </div>
    </header>
  );
}
