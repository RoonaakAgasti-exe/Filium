from contextlib import contextmanager
from datetime import date, datetime, timezone
import pytest
from fastapi.testclient import TestClient
import main
from auth import create_access_token, decode_access_token, get_current_user_id
from db import get_conn
from market_data import PriceUnavailable
from routers import portfolio as portfolio_router

USER_ID = 7

class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows: list = []

    def execute(self, sql, params = None):
        sql = " ".join(sql.split())
        self._conn.seen.append((sql, params))
        for fragment, results in self._conn.sequences.items():
            if fragment in sql:
                self._rows = list(next(results))
                return
        for fragment, rows in self._conn.routes.items():
            if fragment in sql:
                self._rows = list(rows)
                return
        if sql.startswith("SELECT"):
            raise AssertionError(f"Unrouted query: {sql}")
        self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass

class FakeConn:
    def __init__(self, routes: dict | None = None, sequences: dict | None = None):
        self.routes = dict(routes or {})
        self.sequences = {k: iter(v) for k, v in (sequences or {}).items()}
        self.seen: list = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

@pytest.fixture
def client():
    c = TestClient(main.app)
    yield c
    main.app.dependency_overrides.clear()

def as_user(conn, user_id: int = USER_ID):
    main.app.dependency_overrides[get_conn] = lambda: conn
    main.app.dependency_overrides[get_current_user_id] = lambda: user_id

HOLDINGS_JOIN = "FROM holdings h LEFT JOIN companies"
SNAPSHOTS = "FROM portfolio_snapshots"
SPY = "ticker = 'SPY'"

class TestHealth:
    def test_reports_ok_and_the_integration_matrix_when_the_database_answers(self, client, monkeypatch):
        @contextmanager
        def fake_connection():
            yield FakeConn({"SELECT 1": [(1,)]})
        monkeypatch.setattr(main.db, "connection", fake_connection)
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["database"]["connected"] is True
        assert set(body["integrations"]) == {"openai", "llm", "alpaca", "news", "live_quotes", "fmp", "embeddings"}
        assert set(body["integrations"]["llm"]) == {"configured", "model", "endpoint"}
        assert body["integrations"]["llm"]["configured"] == body["integrations"]["openai"]
        assert body["integrations"]["embeddings"] in ("openai", "local")
        assert body["version"] == main.app.version

    def test_names_the_live_embedding_model_not_just_the_provider(self, client, monkeypatch):
        @contextmanager
        def fake_connection():
            yield FakeConn({"SELECT 1": [(1,)]})
        monkeypatch.setattr(main.db, "connection", fake_connection)
        body = client.get("/health").json()
        assert body["embeddings"]["provider"] in ("openai", "local")
        assert body["embeddings"]["model"]
        assert isinstance(body["embeddings"]["dimension"], int)

    def test_reports_an_unknown_embedding_model_instead_of_500ing_on_it(self, client, monkeypatch):
        @contextmanager
        def fake_connection():
            yield FakeConn({"SELECT 1": [(1,)]})
        monkeypatch.setattr(main.db, "connection", fake_connection)
        monkeypatch.setattr(main.embeddings.config, "EMBEDDING_PROVIDER", "local")
        monkeypatch.setattr(main.embeddings.config, "LOCAL_EMBEDDING_MODEL", "some/unlisted-model")
        response = client.get("/health")
        assert response.status_code == 200
        assert "Unknown embedding dimension" in response.json()["embeddings"]["error"]

    def test_still_returns_200_when_the_database_is_unreachable(self, client, monkeypatch):
        def boom():
            raise RuntimeError("could not connect to server")
        monkeypatch.setattr(main.db, "connection", boom)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert "could not connect" in response.json()["database"]["error"]

class TestAuthIsRequired:
    @pytest.mark.parametrize("path", [
        "/portfolio",
        "/portfolio/history",
        "/portfolio/transactions",
        "/portfolio/analytics",
        "/portfolio/vs-benchmark"])
    def test_portfolio_endpoints_reject_an_unauthenticated_request(self, client, path):
        main.app.dependency_overrides[get_conn] = lambda: FakeConn()
        assert client.get(path).status_code == 401

    def test_a_garbage_bearer_token_is_rejected(self, client):
        main.app.dependency_overrides[get_conn] = lambda: FakeConn()
        response = client.get("/portfolio", headers = {"Authorization": "Bearer not-a-real-jwt"})
        assert response.status_code == 401

class TestTokenRoundTrip:
    def test_an_issued_token_decodes_back_to_the_same_user(self):
        token = create_access_token(42, "someone@example.com")
        payload = decode_access_token(token)
        assert payload["sub"] == "42"
        assert payload["email"] == "someone@example.com"
        assert "exp" in payload

class TestPortfolioHistory:
    def test_serialises_dates_as_strings_and_values_as_floats(self, client):
        as_user(FakeConn({SNAPSHOTS: [(date(2024, 3, 1), 100_000, 100_000, 0), (date(2024, 3, 2), 101_500, 90_000, 11_500)]}))
        body = client.get("/portfolio/history").json()
        assert body == [
            {"date":"2024-03-01", "total_value":100_000.0, "cash_value":100_000.0, "holdings_value":0.0},
            {"date":"2024-03-02", "total_value":101_500.0, "cash_value":90_000.0, "holdings_value":11_500.0}]

    def test_a_user_with_no_snapshots_gets_an_empty_list_not_an_error(self, client):
        as_user(FakeConn({SNAPSHOTS: []}))
        response = client.get("/portfolio/history")
        assert response.status_code == 200
        assert response.json() == []

class TestPortfolioTransactions:
    def test_computes_amount_and_passes_through_a_null_realized_pl(self, client):
        executed = datetime(2024, 3, 14, 15, 30, tzinfo = timezone.utc)
        as_user(FakeConn({"FROM transactions t":[(1, "AAPL", "buy", 10, 150.0, None, executed, False, None), (2, "AAPL", "sell", 4, 175.0, 100.0, executed, True, "Sold on a bearish flip")]}))
        body = client.get("/portfolio/transactions").json()
        assert body[0]["amount"] == pytest.approx(1_500.0)
        assert body[0]["realized_pl"] is None
        assert body[0]["explanation"] is None
        assert body[1]["realized_pl"] == pytest.approx(100.0)
        assert body[1]["triggered_by_prediction"] is True
        assert body[1]["explanation"] == "Sold on a bearish flip"
        assert body[0]["executed_at"] == executed.isoformat()

    @pytest.mark.parametrize("limit", [0, -1, 201, 1000])
    def test_rejects_an_out_of_range_limit(self, client, limit):
        as_user(FakeConn({"FROM transactions t":[]}))
        assert client.get(f"/portfolio/transactions?limit={limit}").status_code == 422

    @pytest.mark.parametrize("limit", [1, 20, 200])
    def test_accepts_a_limit_inside_the_range(self, client, limit):
        conn = FakeConn({"FROM transactions t": []})
        as_user(conn)
        assert client.get(f"/portfolio/transactions?limit={limit}").status_code == 200
        assert conn.seen[-1][1] == (USER_ID, limit)

class TestVsBenchmark:
    def test_one_snapshot_is_not_enough_to_compute_a_return(self, client):
        as_user(FakeConn({SNAPSHOTS: [(date(2024, 3, 1), 100_000)]}))
        body = client.get("/portfolio/vs-benchmark").json()
        assert body["portfolio_return"] is None
        assert body["benchmark_return"] is None
        assert "Not enough snapshot history" in body["message"]

    def test_no_snapshots_at_all_takes_the_same_path(self, client):
        as_user(FakeConn({SNAPSHOTS: []}))
        assert client.get("/portfolio/vs-benchmark").json()["portfolio_return"] is None

    def test_a_zero_first_snapshot_reports_why_rather_than_dividing_by_zero(self, client):
        as_user(FakeConn({SNAPSHOTS: [(date(2024, 3, 1), 0), (date(2024, 3, 2), 500)]}))
        body = client.get("/portfolio/vs-benchmark").json()
        assert body["portfolio_return"] is None
        assert "zero value" in body["message"]

    def test_reports_the_portfolio_return_even_when_spy_is_not_ingested(self, client):
        as_user(FakeConn({SNAPSHOTS: [(date(2024, 3, 1), 100_000), (date(2024, 3, 31), 110_000)], SPY: []}))
        body = client.get("/portfolio/vs-benchmark").json()
        assert body["portfolio_return"] == pytest.approx(0.10)
        assert body["benchmark_return"] is None
        assert "fetch_prices.py SPY" in body["message"]
        assert body["start_date"] == "2024-03-01"
        assert body["end_date"] == "2024-03-31"

    def test_matching_the_benchmark_gives_zero_excess_return(self, client):
        as_user(FakeConn(routes = {SNAPSHOTS:[(date(2024, 3, 1), 100_000), (date(2024, 3, 31), 110_000)]}, sequences = {SPY:[[(400.0,)], [(440.0,)]]}))
        body = client.get("/portfolio/vs-benchmark").json()
        assert body["portfolio_return"] == pytest.approx(0.10)
        assert body["benchmark_return"] == pytest.approx(0.10)
        assert body["excess_return"] == pytest.approx(0.0)

    def test_underperforming_the_benchmark_gives_a_negative_excess_return(self, client):
        as_user(FakeConn(routes = {SNAPSHOTS:[(date(2024, 3, 1), 100_000), (date(2024, 3, 31), 102_000)]}, sequences = {SPY:[[(400.0,)], [(440.0,)]]}))
        body = client.get("/portfolio/vs-benchmark").json()
        assert body["portfolio_return"] == pytest.approx(0.02)
        assert body["benchmark_return"] == pytest.approx(0.10)
        assert body["excess_return"] == pytest.approx(-0.08)

    def test_beating_the_benchmark_gives_a_positive_excess_return(self, client):
        as_user(FakeConn(routes = {SNAPSHOTS:[(date(2024, 3, 1), 100_000), (date(2024, 3, 31), 120_000)]}, sequences = {SPY:[[(400.0,)], [(420.0,)]]}))
        body = client.get("/portfolio/vs-benchmark").json()
        assert body["excess_return"] == pytest.approx(0.15)

class TestReadPortfolio:
    def test_values_holdings_at_the_quoted_price(self, client, monkeypatch):
        monkeypatch.setattr(portfolio_router.market_data, "get_prices", lambda conn, tickers:{"AAPL":{"price":120.0}})
        as_user(FakeConn({
            "SELECT DISTINCT ticker FROM holdings":[("AAPL",)],
            "SELECT cash_balance FROM wallets":[(5_000.0,)], HOLDINGS_JOIN:[("AAPL", 10.0, 100.0, "Technology")]}))
        body = client.get("/portfolio").json()
        assert body["cash_balance"] == pytest.approx(5_000.0)
        assert body["holdings_value"] == pytest.approx(1_200.0)
        assert body["total_value"] == pytest.approx(6_200.0)
        assert body["holdings"][0]["unrealized_pl"] == pytest.approx(200.0)
        assert body["holdings"][0]["sector"] == "Technology"

    def test_an_empty_portfolio_still_reports_starting_cash(self, client, monkeypatch):
        asked = []

        def spy_get_prices(conn, tickers):
            asked.append(tickers)
            return {}
        monkeypatch.setattr(portfolio_router.market_data, "get_prices", spy_get_prices)
        as_user(FakeConn({
            "SELECT DISTINCT ticker FROM holdings":[],
            "SELECT cash_balance FROM wallets":[(100_000.0,)], HOLDINGS_JOIN:[]}))
        body = client.get("/portfolio").json()
        assert body["total_value"] == pytest.approx(100_000.0)
        assert body["holdings"] == []
        assert asked == [[]]

class TestExceptionHandlers:
    def _portfolio_raising(self, monkeypatch, exc):
        def raiser(conn, tickers):
            raise exc
        monkeypatch.setattr(portfolio_router.market_data, "get_prices", raiser)
        as_user(FakeConn({
            "SELECT DISTINCT ticker FROM holdings":[("AAPL",)],
            "SELECT cash_balance FROM wallets":[(1.0,)], HOLDINGS_JOIN:[]}))

    def test_a_value_error_becomes_a_400_not_a_500(self, client, monkeypatch):
        self._portfolio_raising(monkeypatch, ValueError("shares must be positive"))
        response = client.get("/portfolio")
        assert response.status_code == 400
        assert response.json()["detail"] == "shares must be positive"

    def test_a_price_outage_becomes_a_503(self, client, monkeypatch):
        self._portfolio_raising(monkeypatch, PriceUnavailable("no price for AAPL"))
        response = client.get("/portfolio")
        assert response.status_code == 503
        assert "no price for AAPL" in response.json()["detail"]

class TestOpenApi:
    def test_every_router_is_mounted(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        for expected in ["/health", "/auth/login", "/query", "/predictions/{ticker}", "/prices/{ticker}", "/news/{ticker}", "/watchlist", "/trade/buy", "/portfolio", "/public/tickers"]:
            assert expected in paths, f"{expected} is not mounted"