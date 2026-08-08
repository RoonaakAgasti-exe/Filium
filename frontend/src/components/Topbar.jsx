import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, Bell, ChevronDown, TrendingUp, TrendingDown } from 'lucide-react';
import { api, getUserEmail } from '../api/client';

export default function Topbar() {
  const [q, setQ] = useState('');
  const [bellOpen, setBellOpen] = useState(false);
  const [acctOpen, setAcctOpen] = useState(false);
  const [signals, setSignals] = useState([]);
  const [signalsLoaded, setSignalsLoaded] = useState(false);
  const navigate = useNavigate();
  const bellRef = useRef(null);
  const acctRef = useRef(null);

  const email = getUserEmail();
  const name = email ? email.split('@')[0].split('_')[0] : 'there';
  const initial = name.charAt(0).toUpperCase();

  useEffect(() => {
    function onClick(e) {
      if (bellRef.current && !bellRef.current.contains(e.target)) setBellOpen(false);
      if (acctRef.current && !acctRef.current.contains(e.target)) setAcctOpen(false);
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
    navigate(`/dashboard?ticker=${ticker}`);
    setQ('');
  }

  return (
    <div className="topbar">
      <div className="topbar-greeting-block">
        <span className="topbar-greeting-title">Greetings! 👋</span>
        <span className="topbar-greeting-sub">Start your day with {name.charAt(0).toUpperCase() + name.slice(1)}</span>
      </div>

      <form className="topbar-search" onSubmit={handleSearch}>
        <Search size={16} strokeWidth={2} />
        <input
          placeholder="Search a ticker, e.g. AAPL"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </form>

      <div className="topbar-right">
        <div className="topbar-bell" ref={bellRef}>
          <button
            style={{ background: 'none', border: 'none', color: 'inherit', display: 'flex' }}
            onClick={() => {
              setBellOpen((v) => !v);
              setAcctOpen(false);
              loadSignals();
            }}
            title="Signal alerts"
          >
            <Bell size={17} strokeWidth={2} />
          </button>
          {signals.length > 0 && <span className="topbar-bell-dot" />}

          {bellOpen && (
            <div className="topbar-dropdown" onClick={(e) => e.stopPropagation()}>
              <p className="topbar-dropdown-title">Signal alerts</p>
              <p className="topbar-dropdown-sub">Latest predictions for your watchlist</p>
              {!signalsLoaded ? (
                <p className="empty-state" style={{ padding: '8px 0' }}>Loading…</p>
              ) : signals.length ? (
                <div className="notif-list">
                  {signals.map((s) => (
                    <a
                      key={s.ticker}
                      className="notif-row"
                      href={`/dashboard?ticker=${s.ticker}`}
                      onClick={(e) => { e.preventDefault(); navigate(`/dashboard?ticker=${s.ticker}`); setBellOpen(false); }}
                    >
                      <div className="ticker-chip">{s.ticker.slice(0, 2)}</div>
                      <div className="notif-text">
                        <strong>
                          {s.ticker} predicted {s.predicted_direction === 'up' ? 'up' : 'down'}
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
                  No signals yet — add a ticker to your watchlist on Predictions.
                </p>
              )}
            </div>
          )}
        </div>

        <div className="topbar-account" ref={acctRef} style={{ position: 'relative' }}>
          <button
            style={{ background: 'none', border: 'none', color: 'inherit', display: 'flex', alignItems: 'center', gap: 8, padding: 0 }}
            onClick={() => { setAcctOpen((v) => !v); setBellOpen(false); }}
          >
            <div className="topbar-avatar">{initial}</div>
            My account
            <ChevronDown size={14} />
          </button>

          {acctOpen && (
            <div className="topbar-dropdown" onClick={(e) => e.stopPropagation()}>
              <p className="topbar-dropdown-title">Paper account</p>
              <p className="topbar-dropdown-sub" style={{ wordBreak: 'break-all' }}>{email}</p>
              <a
                href="/settings"
                className="btn btn-ghost"
                style={{ display: 'block', textAlign: 'center', textDecoration: 'none', fontSize: 13 }}
                onClick={(e) => { e.preventDefault(); navigate('/settings'); setAcctOpen(false); }}
              >
                Manage account
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
