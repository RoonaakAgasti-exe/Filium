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

// No login/signup screen: every visitor gets a silent, standing "paper
// account" the moment the app loads (see ensureSession in api/client.js).
// We just wait for that one-time handshake before mounting the routes.
export default function App() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    ensureSession().finally(() => setReady(true));
  }, []);

  if (!ready) {
    return (
      <div className="boot-screen">
        <div className="boot-mark">FC</div>
        <p>Setting up your paper account…</p>
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
