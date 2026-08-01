import { NavLink, useNavigate } from 'react-router-dom';
import {
  LayoutGrid, MessageCircleMore, LineChart, WalletCards, Settings as SettingsIcon, RotateCcw,
  Bell, FlaskConical, Trophy,
} from 'lucide-react';
import './Layout.css';

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutGrid, end: true },
  { to: '/chat', label: 'Ask AI', icon: MessageCircleMore },
  { to: '/dashboard', label: 'Predictions', icon: LineChart },
  { to: '/portfolio', label: 'Portfolio', icon: WalletCards },
  { to: '/alerts', label: 'Alerts', icon: Bell },
  { to: '/sandbox', label: 'Sandbox', icon: FlaskConical },
  { to: '/leaderboard', label: 'Leaderboard', icon: Trophy },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
];

export default function Layout({ children }) {
  const navigate = useNavigate();

  return (
    <div className="shell">
      <aside className="rail">
        <div className="rail-mark">FC</div>
        <nav className="rail-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `rail-link${isActive ? ' rail-link-active' : ''}`}
              title={item.label}
            >
              <item.icon size={19} strokeWidth={2} />
            </NavLink>
          ))}
        </nav>
        <button
          className="rail-reset"
          onClick={() => navigate('/settings')}
          title="Reset paper account"
        >
          <RotateCcw size={18} strokeWidth={2} />
        </button>
      </aside>

      <div className="shell-body">
        {children}
      </div>
    </div>
  );
}
