import { useState } from 'react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { api } from '../api/client';
import './Chat.css';

export default function Chat() {
  const [ticker, setTicker] = useState('');
  const [question, setQuestion] = useState('');
  const [history, setHistory] = useState([]); // {question, answer, sources}
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleAsk(e) {
    e.preventDefault();
    if (!question.trim()) return;

    setLoading(true);
    setError('');
    const askedQuestion = question;
    setQuestion('');

    try {
      const result = await api.askQuestion(askedQuestion, ticker || null);
      setHistory((h) => [...h, { question: askedQuestion, ...result }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Layout>
      <Topbar />
      <div className="chat-page">
        <div className="chat-intro">
          <h1 className="page-title">Ask the filings</h1>
          <p className="page-sub">
            Questions are answered only from ingested SEC filings — every claim links back to its
            source.
          </p>
        </div>

        <div className="chat-thread">
          {history.length === 0 && !loading && (
            <div className="chat-empty">
              Nothing asked yet. Try: "What does the company say about supply chain risk?"
            </div>
          )}

          {history.map((turn, i) => (
            <div key={i} className="chat-turn">
              <div className="chat-question">{turn.question}</div>
              <div className="chat-answer">{turn.answer}</div>
              {turn.sources?.length > 0 && (
                <div className="chat-sources">
                  {turn.sources.map((s) => (
                    <a
                      key={s.marker}
                      href={s.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="source-card"
                    >
                      <span className="source-marker">[{s.marker}]</span>
                      <span className="source-meta">
                        {s.ticker} {s.filing_type} · {s.filing_date} · {s.section}
                      </span>
                    </a>
                  ))}
                </div>
              )}
            </div>
          ))}

          {loading && <div className="chat-loading">Reading the filings…</div>}
          {error && <div className="chat-error">{error}</div>}
        </div>

        <form className="chat-input-row" onSubmit={handleAsk}>
          <input
            className="ticker-input"
            placeholder="Ticker (optional)"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            maxLength={6}
          />
          <input
            className="question-input"
            placeholder="Ask a question about a filing…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <button type="submit" disabled={loading}>
            Ask
          </button>
        </form>
      </div>
    </Layout>
  );
}
