import { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { TrendingUp, TrendingDown, ArrowLeft, ExternalLink } from 'lucide-react';
import { api } from '../api/client';
import './PublicTicker.css';

function formatPct(n) {
  if (n == null) return '—';
  return `${(n * 100).toFixed(1)}%`;
}

function formatDate(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export default function PublicTicker() {
  const { ticker } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!ticker) return;
    setLoading(true);
    api.getPublicTicker(ticker)
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [ticker]);

  if (loading) {
    return (
      <div className="public-page">
        <div className="public-loading">Loading…</div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="public-page">
        <div className="public-error">
          <h2>Not found</h2>
          <p>{error || 'No public data for this ticker.'}</p>
          <Link to="/" className="btn btn-primary">Go home</Link>
        </div>
      </div>
    );
  }

  const latest = data.latest_prediction;
  const accuracy = data.accuracy_summary;

  return (
    <div className="public-page">
      <header className="public-header">
        <Link to="/" className="public-back">
          <ArrowLeft size={16} /> FinCopilot
        </Link>
        <span className="public-badge">Public track record</span>
      </header>

      <main className="public-main">
        <div className="public-hero">
          <h1 className="public-ticker">{data.ticker}</h1>
          <p className="public-name">{data.name}</p>
          {data.sector && <span className="public-sector">{data.sector}</span>}
        </div>

        <div className="public-grid">
          <div className="card public-card">
            <h3>Current signal</h3>
            {latest ? (
              <div className="public-signal">
                <div className={`public-direction ${latest.predicted_direction}`}>
                  {latest.predicted_direction === 'up' ? <TrendingUp size={28} /> : <TrendingDown size={28} />}
                  <span>{latest.predicted_direction.toUpperCase()}</span>
                </div>
                <div className="public-confidence">
                  <span className="public-conf-value">{formatPct(latest.confidence)}</span>
                  <span className="public-conf-label">confidence</span>
                </div>
                <p className="public-date">Predicted on {formatDate(latest.prediction_date)} for {formatDate(latest.target_date)}</p>
              </div>
            ) : (
              <p className="empty-state">No prediction available.</p>
            )}
          </div>

          <div className="card public-card">
            <h3>Track record</h3>
            {accuracy ? (
              <div className="public-accuracy">
                <div className="public-acc-big">
                  <span className="public-acc-value">{formatPct(accuracy.accuracy)}</span>
                  <span className="public-acc-label">live accuracy</span>
                </div>
                <div className="public-acc-details">
                  <div><span>Resolved</span><strong>{accuracy.resolved_predictions}</strong></div>
                  <div><span>Total</span><strong>{accuracy.total_predictions}</strong></div>
                  <div><span>Correct</span><strong>{accuracy.correct_predictions}</strong></div>
                </div>
              </div>
            ) : (
              <p className="empty-state">No resolved predictions yet.</p>
            )}
          </div>
        </div>

        {data.recent_predictions?.length > 0 && (
          <div className="card public-card">
            <h3>Recent predictions</h3>
            <div className="public-history">
              {data.recent_predictions.map((p, i) => (
                <div key={i} className="public-history-row">
                  <span className={`public-history-dir ${p.predicted_direction}`}>
                    {p.predicted_direction === 'up' ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                  </span>
                  <span className="public-history-date">{formatDate(p.prediction_date)}</span>
                  <span className="public-history-conf">{formatPct(p.confidence)}</span>
                  <span className={`public-history-outcome ${p.actual_direction === p.predicted_direction ? 'gain' : p.actual_direction ? 'loss' : ''}`}>
                    {p.actual_direction ? (p.actual_direction === p.predicted_direction ? 'Correct' : 'Wrong') : 'Pending'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        <footer className="public-footer">
          <p>
            This is a model signal, not investment advice. Past performance does not guarantee future results.
          </p>
          <a href="https://github.com" target="_blank" rel="noreferrer" className="public-link">
            Built with FinCopilot <ExternalLink size={12} />
          </a>
        </footer>
      </main>
    </div>
  );
}
