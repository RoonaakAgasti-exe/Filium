// Defaults to the same-origin /api path that nginx.conf proxies to the
// backend. Hardcoding http://localhost:8000 here worked only for someone
// sitting on the Docker host — for every other visitor "localhost" is
// their own machine, and the browser blocks it as cross-origin besides.
//
// `npm run dev` has no nginx in front of it, so vite.config.js proxies
// /api to the backend for the dev server. Set VITE_API_BASE_URL only
// when the backend is genuinely on a different host.
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

function getToken() {
  return localStorage.getItem('fincopilot_token');
}

async function request(path, options = {}) {
  const token = getToken();
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  };

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* response wasn't JSON — fall back to statusText */
    }
    throw new Error(detail);
  }

  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  register: (email, password) =>
    request('/auth/register', { method: 'POST', body: JSON.stringify({ email, password }) }),
  login: (email, password) =>
    request('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),

  askQuestion: (question, ticker) =>
    request('/query', { method: 'POST', body: JSON.stringify({ question, ticker }) }),

  getPrediction: (ticker) => request(`/predictions/${ticker}`),
  getBacktest: (ticker) => request(`/predictions/${ticker}/backtest`),
  getPredictionHistory: (ticker, limit = 30) => request(`/predictions/${ticker}/history?limit=${limit}`),

  getPriceHistory: (ticker, days = 180) => request(`/prices/${ticker}?days=${days}`),
  getLatestQuote: (ticker) => request(`/prices/${ticker}/latest`),

  getWatchlist: () => request('/watchlist'),
  addToWatchlist: (ticker) =>
    request('/watchlist', { method: 'POST', body: JSON.stringify({ ticker }) }),
  removeFromWatchlist: (ticker) => request(`/watchlist/${ticker}`, { method: 'DELETE' }),

  getPortfolio: () => request('/portfolio'),
  getPortfolioHistory: () => request('/portfolio/history'),
  getBenchmarkComparison: () => request('/portfolio/vs-benchmark'),
  getTransactions: (limit = 20) => request(`/portfolio/transactions?limit=${limit}`),
  buy: (ticker, shares, triggeredByPrediction = false) =>
    request('/trade/buy', {
      method: 'POST',
      body: JSON.stringify({ ticker, shares, triggered_by_prediction: triggeredByPrediction }),
    }),
  sell: (ticker, shares, triggeredByPrediction = false) =>
    request('/trade/sell', {
      method: 'POST',
      body: JSON.stringify({ ticker, shares, triggered_by_prediction: triggeredByPrediction }),
    }),

  // Alerts
  getAlerts: () => request('/alerts'),
  createAlert: (payload) =>
    request('/alerts', { method: 'POST', body: JSON.stringify(payload) }),
  createNaturalAlert: (payload) =>
    request('/alerts/natural', { method: 'POST', body: JSON.stringify(payload) }),
  updateAlert: (id, payload) =>
    request(`/alerts/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteAlert: (id) => request(`/alerts/${id}`, { method: 'DELETE' }),
  checkAlerts: () => request('/alerts/check', { method: 'POST' }),
  getAlertEvents: () => request('/alerts/events'),
  markAlertEventsRead: (ids) =>
    request('/alerts/events/read', { method: 'POST', body: JSON.stringify({ ids }) }),
  getAlertRuleTypes: () => request('/alerts/rule-types'),

  // Sandbox
  getSandboxAvailable: () => request('/sandbox/available'),
  runSandboxBacktest: (payload) =>
    request('/sandbox/backtest', { method: 'POST', body: JSON.stringify(payload) }),

  // Leaderboard / models
  getPredictionModels: () => request('/predictions/models'),
  getLeaderboard: (ticker) =>
    request(ticker ? `/predictions/leaderboard?ticker=${ticker}` : '/predictions/leaderboard'),

  // Public pages
  getPublicTickers: () => request('/public/tickers'),
  getPublicTicker: (ticker) => request(`/public/${ticker}`),

  // News / sentiment
  getNews: (ticker, limit = 30) => request(`/news/${ticker}?limit=${limit}`),
  getSentimentTimeline: (ticker, days = 90) =>
    request(`/news/${ticker}/sentiment-timeline?days=${days}`),

  // Query extras
  askPeerQuestion: (question, tickers) =>
    request('/query/peer', { method: 'POST', body: JSON.stringify({ question, tickers }) }),
  compareFilings: (payload) =>
    request('/query/compare', { method: 'POST', body: JSON.stringify(payload) }),
  getFilings: (ticker) => request(`/query/filings/${ticker}`),
  getQueryHistory: (limit = 20) => request(`/query/history?limit=${limit}`),
};

export function setToken(token) {
  localStorage.setItem('fincopilot_token', token);
}

export function clearToken() {
  localStorage.removeItem('fincopilot_token');
}

export function isLoggedIn() {
  return Boolean(getToken());
}

export function getUserEmail() {
  const token = getToken();
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    return payload.email || null;
  } catch {
    return null;
  }
}

// --- Guest session -----------------------------------------------------
// There is no login/signup screen in this app: instead we keep a
// standing "paper account" per browser. The first time someone visits,
// we mint a random guest identity and register + log it in against the
// real /auth endpoints, then remember the credentials in localStorage so
// the same guest account (and its trade history) is reused on return
// visits — all silently, with no form for the person to fill in.

const GUEST_KEY = 'fincopilot_guest_id';

function getOrCreateGuestId() {
  let id = localStorage.getItem(GUEST_KEY);
  if (!id) {
    id = `guest_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem(GUEST_KEY, id);
  }
  return id;
}

/**
 * Ensures a valid auth token exists, minting a fresh guest account if
 * needed. Pass `fresh: true` to abandon the current guest and start a
 * brand-new paper account (used by the "Reset paper account" action).
 */
export async function ensureSession({ fresh = false } = {}) {
  if (!fresh && isLoggedIn()) return;

  if (fresh) {
    localStorage.removeItem(GUEST_KEY);
    clearToken();
  }

  const guestId = getOrCreateGuestId();
  const email = `${guestId}@paper.fincopilot.local`;
  const password = `${guestId}-pw-9x`; // deterministic per-guest, never shown to the user

  try {
    const result = await api.register(email, password);
    setToken(result.access_token);
  } catch {
    // already registered (returning visitor) — just log in
    const result = await api.login(email, password);
    setToken(result.access_token);
  }
}
