// Layout.jsx — FundFlow Frame & Sidebar Layout
import { NavLink, useNavigate } from 'react-router-dom';
import {
  LayoutGrid, MessageSquare, LineChart, Briefcase,
  FlaskConical, Trophy, Settings as SettingsIcon, RotateCcw,
  Share2
} from 'lucide-react';
import { getUserEmail } from '../api/client';
import './Layout.css';

const NAV_ITEMS = [
  { to: '/', label: 'Overview', icon: LayoutGrid, end: true },
  { to: '/chat', label: 'Filings RAG', icon: MessageSquare },
  { to: '/dashboard', label: 'Predictions', icon: LineChart },
  { to: '/portfolio', label: 'Portfolio', icon: Briefcase },
  { to: '/sandbox', label: 'Sandbox', icon: FlaskConical },
  { to: '/leaderboard', label: 'Leaderboard', icon: Trophy },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
];

export default function Layout({ children }) {
  const navigate = useNavigate();
  const email = getUserEmail();
  const name = email ? email.split('@')[0] : 'User';
  const initial = name.charAt(0).toUpperCase();

  return (
    <div className="fundflow-canvas">
      <div className="fundflow-shell">
        <aside className="fundflow-sidebar">
          <div className="fundflow-logo-mark" onClick={() => navigate('/')} title="FundFlow / Filium">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M4 6C4 4.89543 4.89543 4 6 4H18C19.1046 4 20 4.89543 20 6V9C20 10.1046 19.1046 11 18 11H10C8.89543 11 8 11.8954 8 13V18C8 19.1046 7.10457 20 6 20C4.89543 20 4 19.1046 4 18V6Z" fill="#14141F"/>
              <circle cx="16" cy="16" r="3" fill="url(#logo-grad)"/>
              <defs>
                <linearGradient id="logo-grad" x1="13" y1="13" x2="19" y2="19" gradientUnits="userSpaceOnUse">
                  <stop stopColor="#6D5CFC"/>
                  <stop offset="1" stopColor="#8B7CFC"/>
                </linearGradient>
              </defs>
            </svg>
          </div>

          <nav className="fundflow-nav">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => `fundflow-nav-item${isActive ? ' fundflow-nav-active' : ''}`}
                title={item.label}
              >
                <item.icon size={20} strokeWidth={2.2} />
              </NavLink>
            ))}
          </nav>

          <div className="fundflow-sidebar-bottom">
            <button
              className="fundflow-side-btn"
              onClick={() => navigate('/portfolio')}
              title="Share / Export"
            >
              <Share2 size={18} strokeWidth={2} />
            </button>
            <button
              className="fundflow-side-btn"
              onClick={() => navigate('/settings')}
              title="Reset paper balance / Settings"
            >
              <RotateCcw size={18} strokeWidth={2} />
            </button>
            <div
              className="fundflow-user-avatar-wrap"
              onClick={() => navigate('/settings')}
              title={`Logged in as ${email}`}
            >
              <div className="fundflow-user-avatar">{initial}</div>
              <span className="fundflow-online-dot" />
            </div>
          </div>
        </aside>

        <main className="fundflow-main-viewport">
          {children}
        </main>
      </div>
    </div>
  );
}
