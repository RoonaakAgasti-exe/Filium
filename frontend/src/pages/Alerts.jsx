import { useEffect, useState } from 'react';
import { Bell, Plus, Trash2, ToggleLeft, ToggleRight, Zap, MessageSquare } from 'lucide-react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { api } from '../api/client';
import './Alerts.css';

const RULE_LABELS = {
  prediction_flip: 'Prediction flip',
  price_move: 'Price move %',
  sentiment_below: 'Sentiment below',
  sentiment_above: 'Sentiment above',
  confidence_above: 'Confidence above',
};

function formatThreshold(ruleType, threshold) {
  if (threshold == null) return '—';
  if (ruleType === 'price_move') return `${threshold}%`;
  if (ruleType === 'confidence_above') return `${(threshold * 100).toFixed(0)}%`;
  return threshold.toFixed(2);
}

export default function Alerts() {
  const [alerts, setAlerts] = useState([]);
  const [events, setEvents] = useState([]);
  const [ruleTypes, setRuleTypes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [showNatural, setShowNatural] = useState(false);
  const [newAlert, setNewAlert] = useState({ ticker: '', rule_type: 'prediction_flip', threshold: '' });
  const [naturalText, setNaturalText] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  async function loadAll() {
    try {
      const [a, e, rt] = await Promise.allSettled([
        api.getAlerts(),
        api.getAlertEvents(),
        api.getAlertRuleTypes(),
      ]);
      if (a.status === 'fulfilled') setAlerts(a.value);
      if (e.status === 'fulfilled') setEvents(e.value);
      if (rt.status === 'fulfilled') setRuleTypes(rt.value);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadAll(); }, []);

  async function handleCreate(e) {
    e.preventDefault();
    setCreating(true);
    setError('');
    try {
      const payload = {
        ticker: newAlert.ticker.toUpperCase(),
        rule_type: newAlert.rule_type,
        threshold: newAlert.threshold ? parseFloat(newAlert.threshold) : null,
      };
      await api.createAlert(payload);
      setShowCreate(false);
      setNewAlert({ ticker: '', rule_type: 'prediction_flip', threshold: '' });
      await loadAll();
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  }

  async function handleNatural(e) {
    e.preventDefault();
    setCreating(true);
    setError('');
    try {
      await api.createNaturalAlert({ text: naturalText });
      setShowNatural(false);
      setNaturalText('');
      await loadAll();
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  }

  async function handleToggle(id, current) {
    try {
      await api.updateAlert(id, { is_active: !current });
      await loadAll();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleDelete(id) {
    try {
      await api.deleteAlert(id);
      await loadAll();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleCheckNow() {
    try {
      await api.checkAlerts();
      await loadAll();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleMarkRead(ids) {
    try {
      await api.markAlertEventsRead(ids);
      await loadAll();
    } catch (err) {
      setError(err.message);
    }
  }

  const unreadCount = events.filter((e) => !e.is_read).length;

  return (
    <Layout>
      <Topbar />
      <div className="page-head">
        <div>
          <h1 className="page-title">Alerts</h1>
          <p className="page-sub">Standing rules that fire when predictions flip, prices move, or sentiment shifts.</p>
        </div>
        <div className="alerts-actions">
          <button className="btn btn-secondary" onClick={handleCheckNow}>Check now</button>
          <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
            <Plus size={15} /> New alert
          </button>
          <button className="btn btn-secondary" onClick={() => setShowNatural(true)}>
            <MessageSquare size={15} /> Natural language
          </button>
        </div>
      </div>

      {error && <div className="alert-error">{error}</div>}

      <div className="alerts-grid">
        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Active rules</h3>
            <span className="badge">{alerts.filter((a) => a.is_active).length} active</span>
          </div>
          {loading ? (
            <p className="empty-state">Loading…</p>
          ) : alerts.length === 0 ? (
            <p className="empty-state">No alerts yet. Create one to get notified when signals change.</p>
          ) : (
            <div className="alerts-list">
              {alerts.map((a) => (
                <div key={a.id} className={`alert-row ${!a.is_active ? 'alert-row-disabled' : ''}`}>
                  <div className="alert-row-main">
                    <span className="alert-ticker">{a.ticker}</span>
                    <span className="alert-desc">{a.description}</span>
                    {a.natural_language && (
                      <span className="alert-nl">"{a.natural_language}"</span>
                    )}
                  </div>
                  <div className="alert-row-meta">
                    <span className="alert-threshold">{formatThreshold(a.rule_type, a.threshold)}</span>
                    <button
                      className="icon-btn"
                      onClick={() => handleToggle(a.id, a.is_active)}
                      title={a.is_active ? 'Disable' : 'Enable'}
                    >
                      {a.is_active ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
                    </button>
                    <button className="icon-btn icon-btn-danger" onClick={() => handleDelete(a.id)} title="Delete">
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Recent events</h3>
            {unreadCount > 0 && (
              <button className="btn btn-sm btn-secondary" onClick={() => handleMarkRead(events.filter((e) => !e.is_read).map((e) => e.id))}>
                Mark all read
              </button>
            )}
          </div>
          {events.length === 0 ? (
            <p className="empty-state">No events yet. Alerts fire when conditions are met.</p>
          ) : (
            <div className="events-list">
              {events.slice(0, 20).map((e) => (
                <div key={e.id} className={`event-row ${!e.is_read ? 'event-row-unread' : ''}`}>
                  <div className="event-icon">
                    <Zap size={14} />
                  </div>
                  <div className="event-body">
                    <span className="event-ticker">{e.ticker}</span>
                    <span className="event-msg">{e.message}</span>
                    <span className="event-time">{new Date(e.created_at).toLocaleString()}</span>
                  </div>
                  {!e.is_read && (
                    <button className="btn btn-sm" onClick={() => handleMarkRead([e.id])}>Mark read</button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>Create alert</h3>
              <button className="modal-close" onClick={() => setShowCreate(false)}>×</button>
            </div>
            <form onSubmit={handleCreate}>
              <div className="form-row">
                <label>Ticker</label>
                <input
                  value={newAlert.ticker}
                  onChange={(e) => setNewAlert({ ...newAlert, ticker: e.target.value })}
                  placeholder="AAPL"
                  required
                />
              </div>
              <div className="form-row">
                <label>Rule type</label>
                <select
                  value={newAlert.rule_type}
                  onChange={(e) => setNewAlert({ ...newAlert, rule_type: e.target.value })}
                >
                  {Object.entries(RULE_LABELS).map(([k, v]) => (
                    <option key={k} value={k}>{v}</option>
                  ))}
                </select>
              </div>
              {['price_move', 'sentiment_below', 'sentiment_above', 'confidence_above'].includes(newAlert.rule_type) && (
                <div className="form-row">
                  <label>Threshold</label>
                  <input
                    type="number"
                    step="any"
                    value={newAlert.threshold}
                    onChange={(e) => setNewAlert({ ...newAlert, threshold: e.target.value })}
                    placeholder={newAlert.rule_type === 'price_move' ? '5' : newAlert.rule_type === 'confidence_above' ? '0.75' : '0.0'}
                    required
                  />
                </div>
              )}
              <button className="btn btn-primary" type="submit" disabled={creating}>
                {creating ? 'Creating…' : 'Create alert'}
              </button>
            </form>
          </div>
        </div>
      )}

      {showNatural && (
        <div className="modal-overlay" onClick={() => setShowNatural(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>Natural language alert</h3>
              <button className="modal-close" onClick={() => setShowNatural(false)}>×</button>
            </div>
            <form onSubmit={handleNatural}>
              <div className="form-row">
                <label>Describe the alert</label>
                <textarea
                  value={naturalText}
                  onChange={(e) => setNaturalText(e.target.value)}
                  placeholder="e.g. Notify me if NVDA's sentiment turns negative"
                  rows={3}
                  required
                />
              </div>
              <button className="btn btn-primary" type="submit" disabled={creating}>
                {creating ? 'Parsing…' : 'Create from text'}
              </button>
            </form>
          </div>
        </div>
      )}
    </Layout>
  );
}
