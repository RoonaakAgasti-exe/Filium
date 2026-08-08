import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Chat from './pages/Chat';
import Home from './pages/Home';
import Dashboard from './pages/Dashboard';
import Portfolio from './pages/Portfolio';
import Settings from './pages/Settings';
import Alerts from './pages/Alerts';
import Sandbox from './pages/Sandbox';
import Leaderboard from './pages/Leaderboard';
import PublicTicker from './pages/PublicTicker';
import { ensureSession } from './api/client';
import './tokens.css';

export default function App() {
  const [ready, setReady] = useState(false);
  const [bootError, setBootError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    ensureSession()
      .then(() => { if (!cancelled) setBootError(null); })
      .catch((err) => { if (!cancelled) setBootError(err.message || String(err)); })
      .finally(() => { if (!cancelled) setReady(true); });
    return () => { cancelled = true; };
  }, []);

  if (!ready) {
    return (
      <div className="boot-screen">
        <div className="boot-mark">FC</div>
        <p>Setting up your paper account…</p>
      </div>
    );
  }

  if (bootError) {
    return (
      <div className="boot-screen">
        <div className="boot-mark">FC</div>
        <h2>Couldn’t set up your paper account</h2>
        <p className="boot-error">{bootError}</p>
        <p>
          The API may still be starting. Check that the backend is reachable at{' '}
          <code>/health</code>, then retry.
        </p>
        <button type="button" onClick={() => window.location.reload()}>
          Retry
        </button>
      </div>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/alerts" element={<Alerts />} />
        <Route path="/sandbox" element={<Sandbox />} />
        <Route path="/leaderboard" element={<Leaderboard />} />
        <Route path="/public/:ticker" element={<PublicTicker />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
