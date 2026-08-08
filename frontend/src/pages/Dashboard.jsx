import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
  BarChart, Bar, Cell, ComposedChart, Line,
} from 'recharts';
import { Search, TrendingUp, TrendingDown, Star, Zap } from 'lucide-react';
import Layout from '../components/Layout';
import Topbar from '../components/Topbar';
import { api } from '../api/client';
import './Dashboard.css';

const RANGES = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
];

function formatMoney(n, opts = {}) {
  if (n == null) return '—';
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2, ...opts });
}

function formatPct(n, digits = 1) {
  if (n == null) return '—';
  return `${(n * 100).toFixed(digits)}%`;
}

function ConfidenceRing({ value, direction }) {
  const size = 96;
  const stroke = 9;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(1, value));
  const color = direction === 'up' ? 'var(--color-gain)' : 'var(--color-loss)';
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-border)" strokeWidth={stroke} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
        strokeDasharray={c} strokeDashoffset={c * (1 - pct)} strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: 'stroke-dashoffset 0.6s ease' }}
      />
      <text x="50%" y="47%" textAnchor="middle" fontFamily="var(--font-display)" fontWeight="800" fontSize="20" fill="var(--color-text)">
        {(pct * 100).toFixed(0)}%
      </text>
      <text x="50%" y="64%" textAnchor="middle" fontFamily="var(--font-body)" fontSize="9.5" fill="var(--color-text-faint)" letterSpacing="0.05em">
        CONFIDENCE
      </text>
    </svg>
  );
}

export default function Dashboard() {
  const [params, setParams] = useSearchParams();
  const urlTicker = params.get('ticker') || '';

  const [tickerInput, setTickerInput] = useState(urlTicker);
  const [ticker, setTicker] = useState(urlTicker);
  const [prediction, setPrediction] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [predHistory, setPredHistory] = useState([]);
  const [candles, setCandles] = useState([]);
  const [quote, setQuote] = useState(null);
  const [news, setNews] = useState([]);
  const [sentiment, setSentiment] = useState(null);
  const [watchlist, setWatchlist] = useState([]);
  const [range, setRange] = useState(RANGES[2]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [tradeBusy, setTradeBusy] = useState(false);
  const [tradeMsg, setTradeMsg] = useState('');
  const [calibration, setCalibration] = useState(null);

  useEffect(() => {
    api.getWatchlist().then(setWatchlist).catch(() => {});
  }, []);

  async function loadTicker(t, days) {
    if (!t) return;
    setLoading(true);
    setError('');
    setTradeMsg('');
    setPrediction(null);
    setBacktest(null);
    setPredHistory([]);
    setCandles([]);
    setQuote(null);
    setNews([]);
    setSentiment(null);

    const [
      predResult, backtestResult, histResult, priceResult, quoteResult,
      newsResult, sentimentResult, calibResult,
    ] = await Promise.allSettled([
      api.getPrediction(t),
      api.getBacktest(t),
      api.getPredictionHistory(t, 40),
      api.getPriceHistory(t, days),
      api.getLatestQuote(t),
      api.getNews(t, 12),
      api.getSentimentTimeline(t, days),
      api.getCalibration(t, 5),
    ]);

    if (predResult.status === 'fulfilled') setPrediction(predResult.value);
    if (backtestResult.status === 'fulfilled') setBacktest(backtestResult.value);
    if (histResult.status === 'fulfilled') setPredHistory(histResult.value);
    if (priceResult.status === 'fulfilled') setCandles(priceResult.value.candles);
    if (quoteResult.status === 'fulfilled') setQuote(quoteResult.value);
    if (newsResult.status === 'fulfilled') setNews(newsResult.value.articles || []);
    if (sentimentResult.status === 'fulfilled') setSentiment(sentimentResult.value);
    if (calibResult.status === 'fulfilled') setCalibration(calibResult.value);
    else setCalibration(null);

    if ([predResult, backtestResult, priceResult].every((r) => r.status === 'rejected')) {
      setError(`No data found for ${t.toUpperCase()} yet — try a ticker that's been ingested.`);
    }
    setLoading(false);
  }

  useEffect(() => {
    if (urlTicker) {
      setTicker(urlTicker);
      setTickerInput(urlTicker);
      loadTicker(urlTicker, range.days);
    }
  }, [urlTicker]);

  function handleLookup(e) {
    e.preventDefault();
    const t = tickerInput.trim().toUpperCase();
    if (!t) return;
    setParams({ ticker: t });
  }

  function selectRange(r) {
    setRange(r);
    if (ticker) loadTicker(ticker, r.days);
  }

  async function handleAct(action) {
    if (!ticker || !prediction) return;
    setTradeBusy(true);
    setTradeMsg('');
    try {
      const shares = 1;
      if (action === 'buy') await api.buy(ticker, shares, true);
      else await api.sell(ticker, shares, true);
      setTradeMsg(`${action === 'buy' ? 'Bought' : 'Sold'} 1 share of ${ticker} on this signal.`);
    } catch (err) {
      setTradeMsg(err.message);
    } finally {
      setTradeBusy(false);
    }
  }

  const priceChartData = useMemo(
    () => candles.map((c) => ({ date: c.date, close: c.close, high: c.high, low: c.low })),
    [candles],
  );

  const latestClose = candles.length ? candles[candles.length - 1].close : null;
  const firstClose = candles.length ? candles[0].close : null;
  const periodChange = latestClose != null && firstClose ? (latestClose - firstClose) / firstClose : null;

  const sentimentChart = useMemo(() => {
    if (!sentiment?.points?.length) return [];
    return sentiment.points
      .filter((p) => p.sentiment != null)
      .map((p) => ({
        date: p.date,
        sentiment: p.sentiment,
        close: p.close,
        articles: p.article_count,
      }));
  }, [sentiment]);

  const accuracyTrend = useMemo(() => {
    let correct = 0;
    let resolved = 0;
    return predHistory.map((p) => {
      if (p.correct != null) {
        resolved += 1;
        if (p.correct) correct += 1;
      }
      return {
        date: p.prediction_date,
        confidence: p.confidence,
        correct: p.correct,
        runningAccuracy: resolved ? correct / resolved : null,
      };
    });
  }, [predHistory]);

  return (
    <Layout>
      <Topbar />

      <div className="pred-head">
        <div>
          <h1 className="page-title">Predictions</h1>
          <p className="page-sub">Real prices, a real LSTM signal, and an honestly-tracked accuracy record.</p>
        </div>
        <form className="pred-search" onSubmit={handleLookup}>
          <Search size={15} />
          <input
            className="mono"
            placeholder="Ticker, e.g. AAPL"
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value.toUpperCase())}
            maxLength={6}
          />
          <button className="btn btn-primary" type="submit" disabled={loading}>
            {loading ? '…' : 'Look up'}
          </button>
        </form>
      </div>

      {watchlist.length > 0 && (
        <div className="pred-watch-chips">
          {watchlist.map((w) => (
            <button
              key={w.ticker}
              className={`chip${ticker === w.ticker ? ' chip-active' : ''}`}
              onClick={() => setParams({ ticker: w.ticker })}
            >
              <Star size={11} /> {w.ticker}
            </button>
          ))}
        </div>
      )}

      {error && <p className="empty-state" style={{ textAlign: 'left' }}>{error}</p>}

      {!ticker && !error && (
        <div className="card">
          <p className="empty-state">Look up a ticker, or pick one from your watchlist, to see its signal and price action.</p>
        </div>
      )}

      {ticker && (prediction || candles.length > 0) && (
        <div className="pred-grid">
          <div className="pred-col-main">
            <div className="card">
              <div className="card-head">
                <div>
                  <h3 className="card-title">{ticker} — real price action</h3>
                  {quote && <span className="page-sub mono">Live: {formatMoney(quote.price)}</span>}
                </div>
                <div className="pill-row">
                  {RANGES.map((r) => (
                    <button
                      key={r.label}
                      className={`pill${range.label === r.label ? ' pill-active' : ''}`}
                      onClick={() => selectRange(r)}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
              </div>

              {priceChartData.length > 1 ? (
                <>
                  <div className="pred-price-stats">
                    <span className="stat-value mono">{formatMoney(latestClose)}</span>
                    {periodChange != null && (
                      <span className={`badge ${periodChange >= 0 ? 'badge-gain' : 'badge-loss'}`}>
                        {periodChange >= 0 ? '▲' : '▼'} {(periodChange * 100).toFixed(2)}% over {range.label}
                      </span>
                    )}
                  </div>
                  <ResponsiveContainer width="100%" height={240}>
                    <AreaChart data={priceChartData}>
                      <defs>
                        <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--color-accent)" stopOpacity={0.35} />
                          <stop offset="100%" stopColor="var(--color-accent)" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid vertical={false} stroke="var(--color-border-soft)" />
                      <XAxis dataKey="date" stroke="var(--color-text-faint)" fontSize={11} tickLine={false} axisLine={false} minTickGap={40} />
                      <YAxis stroke="var(--color-text-faint)" fontSize={11} tickLine={false} axisLine={false} domain={['auto', 'auto']} width={56} />
                      <Tooltip
                        contentStyle={{ background: 'var(--color-surface-raised)', border: '1px solid var(--color-border)', borderRadius: 10, fontSize: 12 }}
                        formatter={(v) => [formatMoney(v), 'Close']}
                      />
                      <Area type="monotone" dataKey="close" stroke="var(--color-accent)" strokeWidth={2.5} fill="url(#priceFill)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </>
              ) : (
                <p className="empty-state">No ingested price history for {ticker} yet.</p>
              )}
            </div>

            {accuracyTrend.length > 1 && (
              <div className="card">
                <div className="card-head">
                  <h3 className="card-title">Signal confidence, over time</h3>
                  <span className="page-sub">Green = resolved correct · red = resolved incorrect · grey = pending</span>
                </div>
                <ResponsiveContainer width="100%" height={140}>
                  <BarChart data={accuracyTrend} barCategoryGap="22%">
                    <XAxis dataKey="date" stroke="var(--color-text-faint)" fontSize={10} tickLine={false} axisLine={false} minTickGap={30} />
                    <Tooltip
                      contentStyle={{ background: 'var(--color-surface-raised)', border: '1px solid var(--color-border)', borderRadius: 10, fontSize: 12 }}
                      formatter={(v, n, p) => [`${(v * 100).toFixed(0)}%`, p.payload.correct == null ? 'Pending' : p.payload.correct ? 'Correct' : 'Incorrect']}
                    />
                    <Bar dataKey="confidence" radius={[4, 4, 4, 4]}>
                      {accuracyTrend.map((d, i) => (
                        <Cell key={i} fill={d.correct == null ? 'var(--color-border)' : d.correct ? 'var(--color-gain)' : 'var(--color-loss)'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            {(sentimentChart.length > 0 || news.length > 0) && (
              <div className="card">
                <div className="card-head">
                  <div>
                    <h3 className="card-title">News &amp; sentiment</h3>
                    <span className="page-sub">
                      The FinBERT score the augmented model actually trains on.
                    </span>
                  </div>
                  {sentiment?.average_sentiment != null && (
                    <span className={`badge ${sentiment.average_sentiment >= 0 ? 'badge-gain' : 'badge-loss'}`}>
                      avg {sentiment.average_sentiment >= 0 ? '+' : ''}
                      {sentiment.average_sentiment.toFixed(2)} over {sentiment.days_with_sentiment} day(s)
                    </span>
                  )}
                </div>

                {sentimentChart.length > 1 ? (
                  <ResponsiveContainer width="100%" height={180}>
                    <ComposedChart data={sentimentChart}>
                      <CartesianGrid vertical={false} stroke="var(--color-border-soft)" />
                      <XAxis dataKey="date" stroke="var(--color-text-faint)" fontSize={10} tickLine={false} axisLine={false} minTickGap={40} />
                      {}
                      <YAxis yAxisId="sentiment" stroke="var(--color-text-faint)" fontSize={10} tickLine={false} axisLine={false} domain={[-1, 1]} width={40} />
                      <YAxis yAxisId="price" orientation="right" stroke="var(--color-text-faint)" fontSize={10} tickLine={false} axisLine={false} domain={['auto', 'auto']} width={52} />
                      <Tooltip
                        contentStyle={{ background: 'var(--color-surface-raised)', border: '1px solid var(--color-border)', borderRadius: 10, fontSize: 12 }}
                        formatter={(v, n) => (
                          n === 'sentiment'
                            ? [v.toFixed(3), 'Sentiment']
                            : [formatMoney(v), 'Close']
                        )}
                      />
                      <Bar yAxisId="sentiment" dataKey="sentiment" radius={[3, 3, 3, 3]}>
                        {sentimentChart.map((d, i) => (
                          <Cell key={i} fill={d.sentiment >= 0 ? 'var(--color-gain)' : 'var(--color-loss)'} />
                        ))}
                      </Bar>
                      <Line yAxisId="price" type="monotone" dataKey="close" stroke="var(--color-accent)" strokeWidth={2} dot={false} connectNulls />
                    </ComposedChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="empty-state">
                    No scored sentiment for {ticker} yet — run{' '}
                    <code>ingestion/fetch_news.py</code> then <code>ml/sentiment.py</code>.
                  </p>
                )}

                {news.length > 0 && (
                  <div className="news-list">
                    {news.map((a, i) => (
                      <a
                        key={i}
                        className="news-row"
                        href={a.url || undefined}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <span
                          className="news-dot"
                          style={{
                            background: a.sentiment_score == null
                              ? 'var(--color-border)'
                              : a.sentiment_score > 0.05
                                ? 'var(--color-gain)'
                                : a.sentiment_score < -0.05
                                  ? 'var(--color-loss)'
                                  : 'var(--color-pending)',
                          }}
                          title={a.sentiment_score == null
                            ? 'Not scored yet'
                            : `FinBERT ${a.sentiment_score >= 0 ? '+' : ''}${a.sentiment_score.toFixed(2)}`}
                        />
                        <span className="news-headline">{a.headline}</span>
                        <span className="news-meta mono">{a.published_date}</span>
                      </a>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="pred-col-side">
            {prediction && (
              <div className="card pred-signal-card">
                <div className="card-head">
                  <h3 className="card-title">Latest signal</h3>
                  <span className={`badge ${prediction.predicted_direction === 'up' ? 'badge-gain' : 'badge-loss'}`}>
                    {prediction.predicted_direction === 'up' ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                    {prediction.predicted_direction === 'up' ? ' UP' : ' DOWN'}
                  </span>
                </div>
                <div className="pred-ring-row">
                  <ConfidenceRing value={prediction.confidence} direction={prediction.predicted_direction} />
                  <div className="pred-ring-meta">
                    <div><span className="stat-label">Made on</span><span className="mono">{prediction.prediction_date}</span></div>
                    <div><span className="stat-label">Target date</span><span className="mono">{prediction.target_date}</span></div>
                    {prediction.actual_direction && (
                      <div>
                        <span className="stat-label">Outcome</span>
                        <span className={prediction.actual_direction === prediction.predicted_direction ? 'gain' : 'loss'}>
                          {prediction.actual_direction === prediction.predicted_direction ? 'Correct' : 'Incorrect'}
                        </span>
                      </div>
                    )}
                  </div>
                </div>

                <div className="pred-act-row">
                  <button className="btn btn-primary" disabled={tradeBusy} onClick={() => handleAct('buy')}>
                    <Zap size={13} /> Act — buy 1 sh
                  </button>
                  <button className="btn btn-ghost" disabled={tradeBusy} onClick={() => handleAct('sell')}>
                    Sell 1 sh
                  </button>
                </div>
                {tradeMsg && <p className="page-sub" style={{ marginTop: 8 }}>{tradeMsg}</p>}
                <p className="pred-disclaimer">Paper trading only — this is a model signal, not investment advice.</p>
              </div>
            )}

            {backtest && (
              <div className="card">
                <div className="card-head">
                  <h3 className="card-title">Track record</h3>
                </div>
                <div className="backtest-stats">
                  <div className="backtest-stat">
                    <span className="backtest-stat-value mono">{formatPct(backtest.accuracy)}</span>
                    <span className="backtest-stat-label">accuracy</span>
                  </div>
                  <div className="backtest-stat">
                    <span className="backtest-stat-value mono">{backtest.correct_predictions}</span>
                    <span className="backtest-stat-label">correct</span>
                  </div>
                  <div className="backtest-stat">
                    <span className="backtest-stat-value mono">{backtest.resolved_predictions}</span>
                    <span className="backtest-stat-label">resolved</span>
                  </div>
                  <div className="backtest-stat">
                    <span className="backtest-stat-value mono">{backtest.total_predictions}</span>
                    <span className="backtest-stat-label">total</span>
                  </div>
                </div>
                {backtest.accuracy == null && (
                  <p className="page-sub" style={{ marginTop: 10 }}>
                    No accuracy yet — a prediction only counts once the nightly
                    backtest has a real closing price to score it against.
                  </p>
                )}
              </div>
            )}

            {calibration && calibration.num_resolved >= 3 && (
              <div className="card">
                <div className="card-head">
                  <h3 className="card-title">Calibration</h3>
                  <span className="page-sub mono">{calibration.num_resolved} resolved</span>
                </div>
                <div className="calib-scores">
                  <div className="calib-score">
                    <span className="calib-score-val mono">
                      {calibration.brier_score != null ? calibration.brier_score.toFixed(3) : '—'}
                    </span>
                    <span className="calib-score-label">Brier score</span>
                  </div>
                  <div className="calib-score">
                    <span className="calib-score-val mono">
                      {calibration.expected_calibration_error != null
                        ? formatPct(calibration.expected_calibration_error)
                        : '—'}
                    </span>
                    <span className="calib-score-label">Cal. error (ECE)</span>
                  </div>
                </div>
                {calibration.bins?.length > 0 && (
                  <div className="calib-chart">
                    <span className="calib-axis-label">Predicted</span>
                    {calibration.bins.map((b, i) => (
                      <div key={i} className="calib-bin" title={`${formatPct(b.bin_center)} predicted → ${formatPct(b.actual_rate)} actual (${b.count} predictions)`}>
                        <div className="calib-bars">
                          <div
                            className="calib-bar calib-bar-actual"
                            style={{ height: `${Math.max(2, (b.actual_rate ?? 0) * 80)}px` }}
                          />
                          <div
                            className="calib-bar calib-bar-ideal"
                            style={{ height: `${Math.max(2, b.bin_center * 80)}px` }}
                          />
                        </div>
                        <span className="calib-bin-label mono">{formatPct(b.bin_center)}</span>
                      </div>
                    ))}
                    <p className="calib-legend">
                      <span className="calib-dot calib-dot-actual"/> Actual &nbsp;
                      <span className="calib-dot calib-dot-ideal"/> Ideal
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </Layout>
  );
}
