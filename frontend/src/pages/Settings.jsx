// Settings.jsx — User settings page.
import { useEffect, useState } from 'react';
import { RotateCcw, ShieldCheck, Zap } from 'lucide-react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { ensureSession, getUserEmail } from '../api/client';
import './Settings.css';

const AUTO_INVEST_KEY = 'fincopilot_auto_invest';

export default function Settings() {
  const [autoInvest, setAutoInvest] = useState(false);
  const [threshold, setThreshold] = useState(75);
  const [resetting, setResetting] = useState(false);
  const email = getUserEmail();

  useEffect(() => {
    const saved = localStorage.getItem(AUTO_INVEST_KEY);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        setAutoInvest(Boolean(parsed.enabled));
        if (parsed.threshold) setThreshold(parsed.threshold);
      } catch {

      }
    }
  }, []);

  function persist(next) {
    localStorage.setItem(AUTO_INVEST_KEY, JSON.stringify(next));
  }

  function toggleAutoInvest() {
    const next = { enabled: !autoInvest, threshold };
    setAutoInvest(next.enabled);
    persist(next);
  }

  function updateThreshold(v) {
    setThreshold(v);
    persist({ enabled: autoInvest, threshold: v });
  }

  async function handleReset() {
    if (!window.confirm('Start a brand-new paper account? Your current trade history and holdings will no longer be reachable from this browser.')) {
      return;
    }
    setResetting(true);
    try {
      await ensureSession({ fresh: true });
      window.location.href = '/';
    } finally {
      setResetting(false);
    }
  }

  return (
    <Layout>
      <Topbar />

      <div className="page-head">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Your paper account, trading preferences, and data.</p>
        </div>
      </div>

      <div className="settings-grid">
        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Account</h3>
            <span className="badge badge-gain"><ShieldCheck size={12} /> Paper only</span>
          </div>
          <p className="page-sub" style={{ marginBottom: 6 }}>Signed in as</p>
          <p className="mono" style={{ wordBreak: 'break-all', fontSize: 13.5 }}>{email}</p>
          <p className="page-sub" style={{ marginTop: 14 }}>
            There's no password to remember — this browser holds a standing guest
            paper-trading account created automatically the first time you visited.
          </p>
        </div>

        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Auto-invest on strong signals</h3>
            <button
              className={`switch ${autoInvest ? 'switch-on' : ''}`}
              onClick={toggleAutoInvest}
              aria-pressed={autoInvest}
              title="Toggle auto-invest"
            >
              <span className="switch-knob" />
            </button>
          </div>
          <p className="page-sub">
            <Zap size={13} style={{ verticalAlign: -2 }} /> When enabled, the Predictions page will
            highlight a one-click "Act" prompt any time a watchlist ticker's signal confidence
            clears your threshold below.
          </p>
          <div className="threshold-row">
            <input
              type="range"
              min="50"
              max="95"
              step="5"
              value={threshold}
              onChange={(e) => updateThreshold(Number(e.target.value))}
              disabled={!autoInvest}
            />
            <span className="mono threshold-value">{threshold}%</span>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Reset paper account</h3>
          </div>
          <p className="page-sub" style={{ marginBottom: 14 }}>
            Wipe this browser's guest identity and start over with a fresh $100,000
            paper balance and no trade history.
          </p>
          <button className="btn btn-ghost" onClick={handleReset} disabled={resetting}>
            <RotateCcw size={14} style={{ verticalAlign: -2, marginRight: 6 }} />
            {resetting ? 'Resetting…' : 'Reset paper account'}
          </button>
        </div>
      </div>
    </Layout>
  );
}
