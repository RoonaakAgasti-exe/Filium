// client.js — API client for backend communication.
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
      if (Array.isArray(body.detail)) {
        detail = body.detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
      } else if (body.detail) {
        detail = body.detail;
      }
    } catch {

    }
    const error = new Error(detail);
    error.status = res.status;
    throw error;
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
  getCalibration: (ticker, bins = 5) =>
    request(`/predictions/${ticker}/calibration?bins=${bins}`),
  getPredictionHistory: (ticker, limit = 30) => request(`/predictions/${ticker}/history?limit=${limit}`),

  getPriceHistory: (ticker, days = 180) => request(`/prices/${ticker}?days=${days}`),
  getLatestQuote: (ticker) => request(`/prices/${ticker}/latest`),

  getWatchlist: () => request('/watchlist'),
  addToWatchlist: (ticker) =>
    request('/watchlist', { method: 'POST', body: JSON.stringify({ ticker }) }),
  removeFromWatchlist: (ticker) => request(`/watchlist/${ticker}`, { method: 'DELETE' }),

  getPortfolio: () => request('/portfolio'),
  getPortfolioHistory: () => request('/portfolio/history'),
  getPortfolioAnalytics: () => request('/portfolio/analytics'),
  getBenchmarkComparison: () => request('/portfolio/vs-benchmark'),
  getTransactions: (limit = 20) => request(`/portfolio/transactions?limit=${limit}`),
  depositCash: (amount, mode = 'add') =>
    request('/wallet/deposit', {
      method: 'POST',
      body: JSON.stringify({ amount, mode }),
    }),
  resetCash: () =>
    request('/wallet/deposit', {
      method: 'POST',
      body: JSON.stringify({ mode: 'set' }),
    }),
  getCompany: (ticker) => request(`/companies/${ticker}`),
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
  markAlertEventsRead: (eventIds) =>
    request('/alerts/events/read', {
      method: 'POST',
      body: JSON.stringify({ event_ids: eventIds }),
    }),
  getAlertRuleTypes: () => request('/alerts/rule-types'),

  getSandboxAvailable: () => request('/sandbox/available'),
  runSandboxBacktest: (payload) =>
    request('/sandbox/backtest', { method: 'POST', body: JSON.stringify(payload) }),

  getPredictionModels: () => request('/predictions/models'),
  getLeaderboard: (ticker) =>
    request(ticker ? `/predictions/leaderboard?ticker=${ticker}` : '/predictions/leaderboard'),

  getPublicTickers: () => request('/public/tickers'),
  getPublicTicker: (ticker) => request(`/public/${ticker}`),

  getNews: (ticker, limit = 30) => request(`/news/${ticker}?limit=${limit}`),
  getSentimentTimeline: (ticker, days = 90) =>
    request(`/news/${ticker}/sentiment-timeline?days=${days}`),

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

const GUEST_KEY = 'fincopilot_guest_id';

const GUEST_EMAIL_DOMAIN = 'paper.fincopilot.app';

function getOrCreateGuestId() {
  let id = localStorage.getItem(GUEST_KEY);
  if (!id) {
    id = `guest_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem(GUEST_KEY, id);
  }
  return id;
}

export async function ensureSession({ fresh = false } = {}) {
  if (!fresh && isLoggedIn()) return;

  if (fresh) {
    localStorage.removeItem(GUEST_KEY);
    clearToken();
  }

  const guestId = getOrCreateGuestId();
  const email = `${guestId}@${GUEST_EMAIL_DOMAIN}`;
  const password = `${guestId}-pw-9x`;

  let result;
  try {
    result = await api.register(email, password);
  } catch (err) {
    if (err.status !== 409) throw err;
    result = await api.login(email, password);
  }

  if (!result?.access_token) {
    throw new Error('Auth succeeded but returned no access token');
  }
  setToken(result.access_token);
}
