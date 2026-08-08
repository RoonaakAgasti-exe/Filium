// Chat.jsx — RAG chat interface page.
import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { api } from '../api/client';
import './Chat.css';

const MODES = [
  { id: 'ask', label: 'Ask' },
  { id: 'peer', label: 'Compare peers' },
  { id: 'filings', label: 'Compare filings' },
];

const PLACEHOLDERS = {
  ask: 'Ask a question about a filing…',
  peer: 'Ask the same question of every company above…',
  filings: 'What changed between these two filings?',
};

function Source({ source }) {
  const [open, setOpen] = useState(false);
  const hasExcerpt = Boolean(source.excerpt);

  return (
    <div className={`source-card${open ? ' source-card-open' : ''}`}>
      <button
        type="button"
        className="source-head"
        onClick={() => setOpen((v) => !v)}
        disabled={!hasExcerpt}
        aria-expanded={open}
      >
        {hasExcerpt
          ? (open ? <ChevronDown size={13} /> : <ChevronRight size={13} />)
          : <span className="source-head-spacer" />}
        <span className="source-marker">[{source.marker}]</span>
        <span className="source-meta">
          {source.ticker} {source.filing_type} · {source.filing_date} · {source.section}
        </span>
      </button>

      {open && hasExcerpt && (
        <div className="source-body">
          <blockquote className="source-excerpt">
            {source.excerpt}
            {}
            {source.excerpt_truncated && (
              <span className="source-truncated"> (excerpt truncated)</span>
            )}
          </blockquote>
          {source.source_url && (
            <a
              className="source-open"
              href={source.source_url}
              target="_blank"
              rel="noreferrer"
            >
              Open filing <ExternalLink size={11} />
            </a>
          )}
        </div>
      )}
    </div>
  );
}

function SourceList({ sources }) {
  if (!sources?.length) return null;
  return (
    <div className="chat-sources">
      {sources.map((s) => <Source key={s.marker} source={s} />)}
    </div>
  );
}

function Turn({ turn }) {
  return (
    <div className="chat-turn">
      <div className="chat-question">{turn.question}</div>

      {turn.fromHistory && turn.timestamp && (
        <span className="chat-history-meta mono">
          {new Date(turn.timestamp).toLocaleString()}
        </span>
      )}

      {}
      {turn.generated === false && (
        <span
          className="chat-extractive-chip"
          title="No answer-writing model is configured, so these are the retrieved passages verbatim rather than a written answer."
        >
          Retrieved passages
        </span>
      )}

      {}
      {turn.tickers_missing?.length > 0 && (
        <div className="chat-notice">
          Nothing ingested for {turn.tickers_missing.join(', ')} — this answer
          covers only {turn.tickers_covered?.join(', ') || 'the rest'}.
        </div>
      )}

      {turn.earlier_filing && turn.later_filing && (
        <div className="chat-compare-heads">
          <span>
            <strong>Earlier</strong> {turn.earlier_filing.filing_type}{' '}
            {turn.earlier_filing.filing_date}
          </span>
          <span aria-hidden="true">→</span>
          <span>
            <strong>Later</strong> {turn.later_filing.filing_type}{' '}
            {turn.later_filing.filing_date}
          </span>
        </div>
      )}

      <div className="chat-answer">{turn.answer}</div>
      <SourceList sources={turn.sources} />
    </div>
  );
}

export default function Chat() {
  const [mode, setMode] = useState('ask');
  const [ticker, setTicker] = useState('');
  const [peerTickers, setPeerTickers] = useState('');
  const [question, setQuestion] = useState('');
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.getQueryHistory(30)
      .then((rows) => {
        if (!rows?.length) return;
        const past = [...rows].reverse().map((row) => ({
          question: row.question,
          answer: row.answer,
          sources: [],
          fromHistory: true,
          timestamp: row.timestamp,
        }));
        setHistory((h) => (h.length ? h : past));
      })
      .catch(() => {});
  }, []);

  const [filings, setFilings] = useState([]);
  const [filingsLoading, setFilingsLoading] = useState(false);
  const [earlierId, setEarlierId] = useState('');
  const [laterId, setLaterId] = useState('');

  async function loadFilings(t) {
    const symbol = t.trim().toUpperCase();
    if (!symbol) return;
    setFilingsLoading(true);
    setError('');
    try {
      const result = await api.getFilings(symbol);
      setFilings(result.filings || []);
      if (result.filings?.length >= 2) {
        setLaterId(String(result.filings[0].id));
        setEarlierId(String(result.filings[1].id));
      } else {
        setLaterId('');
        setEarlierId('');
      }
    } catch (err) {
      setFilings([]);
      setError(err.message);
    } finally {
      setFilingsLoading(false);
    }
  }

  function switchMode(next) {
    setMode(next);
    setError('');
    if (next === 'filings' && ticker.trim() && !filings.length) {
      loadFilings(ticker);
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!question.trim()) return;

    const asked = question;
    setLoading(true);
    setError('');
    setQuestion('');

    try {
      let result;
      if (mode === 'peer') {
        const list = peerTickers
          .split(',')
          .map((t) => t.trim().toUpperCase())
          .filter(Boolean);
        if (!list.length) {
          throw new Error('Enter at least one ticker, separated by commas.');
        }
        result = await api.askPeerQuestion(asked, list);
      } else if (mode === 'filings') {
        if (!earlierId || !laterId) {
          throw new Error('Pick two filings to compare.');
        }
        if (earlierId === laterId) {
          throw new Error('Pick two different filings to compare.');
        }
        result = await api.compareFilings({
          ticker: ticker.trim().toUpperCase(),
          question: asked,
          earlier_filing_id: Number(earlierId),
          later_filing_id: Number(laterId),
        });
      } else {
        result = await api.askQuestion(asked, ticker.trim().toUpperCase() || null);
      }
      setHistory((h) => [...h, { question: asked, ...result }]);
    } catch (err) {
      setError(err.message);
      setQuestion(asked);
    } finally {
      setLoading(false);
    }
  }

  const canSubmit = !loading && (mode !== 'filings' || (earlierId && laterId));

  return (
    <Layout>
      <Topbar />
      <div className="chat-page">
        <div className="chat-intro">
          <h1 className="page-title">Ask the filings</h1>
          <p className="page-sub">
            Questions are answered only from ingested SEC filings and earnings calls — every
            claim links back to its source, and you can open the passage it came from.
          </p>
        </div>

        <div className="pill-row chat-modes">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              className={`pill${mode === m.id ? ' pill-active' : ''}`}
              onClick={() => switchMode(m.id)}
            >
              {m.label}
            </button>
          ))}
        </div>

        <div className="chat-thread">
          {history.length === 0 && !loading && (
            <div className="chat-empty">
              {mode === 'ask' && 'Nothing asked yet. Try: "What does the company say about supply chain risk?"'}
              {mode === 'peer' && 'List a few tickers, then ask one question of all of them at once.'}
              {mode === 'filings' && 'Pick a ticker and two filings to see what changed between them.'}
            </div>
          )}

          {history.map((turn, i) => <Turn key={i} turn={turn} />)}

          {loading && <div className="chat-loading">Reading the filings…</div>}
          {error && <div className="chat-error">{error}</div>}
        </div>

        <form className="chat-input-form" onSubmit={handleSubmit}>
          {mode === 'peer' ? (
            <input
              className="peer-input mono"
              placeholder="Tickers, comma separated — e.g. AAPL, MSFT, NVDA"
              value={peerTickers}
              onChange={(e) => setPeerTickers(e.target.value.toUpperCase())}
            />
          ) : (
            <div className="chat-scope-row">
              <input
                className="ticker-input"
                placeholder={mode === 'filings' ? 'Ticker' : 'Ticker (optional)'}
                value={ticker}
                onChange={(e) => setTicker(e.target.value.toUpperCase())}
                onBlur={() => mode === 'filings' && loadFilings(ticker)}
                maxLength={6}
              />

              {mode === 'filings' && (
                <>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => loadFilings(ticker)}
                    disabled={filingsLoading || !ticker.trim()}
                  >
                    {filingsLoading ? 'Loading…' : 'Load filings'}
                  </button>

                  {filings.length >= 2 ? (
                    <>
                      <select value={earlierId} onChange={(e) => setEarlierId(e.target.value)}>
                        {filings.map((f) => (
                          <option key={f.id} value={f.id}>
                            Earlier: {f.filing_type} {f.filing_date}
                          </option>
                        ))}
                      </select>
                      <select value={laterId} onChange={(e) => setLaterId(e.target.value)}>
                        {filings.map((f) => (
                          <option key={f.id} value={f.id}>
                            Later: {f.filing_type} {f.filing_date}
                          </option>
                        ))}
                      </select>
                    </>
                  ) : (
                    !filingsLoading && ticker.trim() && (
                      <span className="chat-scope-hint">
                        {filings.length === 1
                          ? 'Only one filing ingested — a comparison needs two.'
                          : 'No filings loaded yet.'}
                      </span>
                    )
                  )}
                </>
              )}
            </div>
          )}

          <div className="chat-input-row">
            <input
              className="question-input"
              placeholder={PLACEHOLDERS[mode]}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
            />
            <button type="submit" disabled={!canSubmit}>
              {loading ? '…' : 'Ask'}
            </button>
          </div>
        </form>
      </div>
    </Layout>
  );
}
