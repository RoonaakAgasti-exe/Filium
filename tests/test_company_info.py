import pytest
import company_info
import config

@pytest.fixture(autouse = True)
def _clear_cache():
    company_info.clear_cache()
    yield
    company_info.clear_cache()

class FakeCursor:
    def __init__(self, db):
        self._db = db
        self._result = None
        self.rowcount = 0
        self.executed = []

    def execute(self, sql, params = ()):
        self.executed.append((" ".join(sql.split()), params))
        normalised = " ".join(sql.split()).upper()
        if normalised.startswith("SELECT NAME, SECTOR FROM COMPANIES"):
            row = self._db.get(params[0])
            self._result = None if row is None else (row["name"], row["sector"])
            return
        if normalised.startswith("SELECT TICKER FROM COMPANIES"):
            self._result = [(t,) for t, r in sorted(self._db.items()) if not r["sector"] or not r["name"] or r["name"].upper() == t]
            return
        if normalised.startswith("UPDATE COMPANIES"):
            name, sector, cik, ticker = params
            row = self._db.get(ticker)
            if row is None:
                self.rowcount = 0
                return
            if name is not None:
                row["name"] = name
            if not row["sector"]:
                row["sector"] = sector
            if not row.get("cik"):
                row["cik"] = cik
            self.rowcount = 1
            return
        raise AssertionError(f"unexpected SQL: {sql}")
    
    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._result or []

    def close(self):
        pass

class FakeConn:
    def __init__(self, companies):
        self.db = {t:{"name":v[0], "sector":v[1], "cik":None} for t, v in companies.items()}
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

@pytest.fixture
def no_fmp(monkeypatch):
    monkeypatch.setattr(config, "FMP_ENABLED", False)

class TestNeedsEnrichment:
    def test_a_placeholder_row_needs_it(self, no_fmp):
        conn = FakeConn({"AAPL":("AAPL", None)})
        assert company_info.needs_enrichment(conn, "AAPL") is True

    def test_a_missing_row_needs_it(self, no_fmp):
        assert company_info.needs_enrichment(FakeConn({}), "AAPL") is True

    def test_a_name_without_a_sector_still_needs_it(self, no_fmp):
        conn = FakeConn({"AAPL":("Apple Inc.", None)})
        assert company_info.needs_enrichment(conn, "AAPL") is True

    def test_a_populated_row_does_not(self, no_fmp):
        conn = FakeConn({"AAPL":("Apple Inc.", "Technology")})
        assert company_info.needs_enrichment(conn, "AAPL") is False

class TestSeedTier:
    def test_fills_a_known_ticker_without_any_api_key(self, no_fmp):
        conn = FakeConn({"AAPL":("AAPL", None)})
        assert company_info.enrich(conn, "AAPL") is True
        assert conn.db["AAPL"]["name"] == "Apple Inc."
        assert conn.db["AAPL"]["sector"] == "Technology"

    def test_an_unknown_ticker_is_left_alone_rather_than_guessed(self, no_fmp):
        conn = FakeConn({"ZZZZ":("ZZZZ", None)})
        assert company_info.enrich(conn, "ZZZZ") is False
        assert conn.db["ZZZZ"] == {"name":"ZZZZ", "sector":None, "cik":None}

    def test_the_benchmark_is_covered_so_it_is_not_unclassified(self, no_fmp):
        conn = FakeConn({"SPY":("SPY", None)})
        assert company_info.enrich(conn, "SPY") is True
        assert conn.db["SPY"]["sector"]

class TestDoesNotClobber:
    def test_a_populated_row_is_not_touched(self, no_fmp):
        conn = FakeConn({"AAPL":("A Better Name", "Tech Sector")})
        assert company_info.enrich(conn, "AAPL") is False
        assert conn.db["AAPL"]["name"] == "A Better Name"
        assert conn.db["AAPL"]["sector"] == "Tech Sector"

    def test_running_twice_changes_nothing_the_second_time(self, no_fmp):
        conn = FakeConn({"MSFT":("MSFT", None)})
        assert company_info.enrich(conn, "MSFT") is True
        after_first = dict(conn.db["MSFT"])
        assert company_info.enrich(conn, "MSFT") is False
        assert conn.db["MSFT"] == after_first

    def test_enrichment_does_not_commit_on_its_own(self, no_fmp):
        conn = FakeConn({"AAPL":("AAPL", None)})
        company_info.enrich(conn, "AAPL")
        assert conn.committed == 0

class TestFmpTier:
    def _fmp(self, monkeypatch, payload, status = 200):
        monkeypatch.setattr(config, "FMP_ENABLED", True)
        monkeypatch.setattr(config, "FMP_API_KEY", "test-key")
        class _Resp:
            status_code = status
            def json(self):
                return payload
        monkeypatch.setattr(company_info.requests, "get", lambda *a, **k: _Resp())

    def test_prefers_fmp_over_the_seed_table(self, monkeypatch):
        self._fmp(monkeypatch, [{"companyName":"Apple Computer Co", "sector":"Electronics", "cik":"0000320193"}])
        conn = FakeConn({"AAPL":("AAPL", None)})
        company_info.enrich(conn, "AAPL")
        assert conn.db["AAPL"]["name"] == "Apple Computer Co"
        assert conn.db["AAPL"]["sector"] == "Electronics"
        assert conn.db["AAPL"]["cik"] == "0000320193"

    def test_covers_a_ticker_the_seed_table_has_never_heard_of(self, monkeypatch):
        self._fmp(monkeypatch, [{"companyName":"Johnson & Johnson", "sector":"Healthcare", "cik":None}])
        conn = FakeConn({"JNJ":("JNJ", None)})
        assert company_info.enrich(conn, "JNJ") is True
        assert conn.db["JNJ"]["sector"] == "Healthcare"

    def test_falls_back_to_the_seed_when_fmp_errors(self, monkeypatch):
        self._fmp(monkeypatch, {"Error Message":"Invalid API KEY"})
        conn = FakeConn({"AAPL":("AAPL", None)})
        assert company_info.enrich(conn, "AAPL") is True
        assert conn.db["AAPL"]["sector"] == "Technology"

    def test_falls_back_to_the_seed_when_the_request_raises(self, monkeypatch):
        monkeypatch.setattr(config, "FMP_ENABLED", True)
        def _boom(*a, **k):
            raise company_info.requests.RequestException("connection reset")
        monkeypatch.setattr(company_info.requests, "get", _boom)
        conn = FakeConn({"AAPL":("AAPL", None)})
        assert company_info.enrich(conn, "AAPL") is True
        assert conn.db["AAPL"]["sector"] == "Technology"

    def test_an_empty_fmp_result_is_a_miss_not_a_crash(self, monkeypatch):
        self._fmp(monkeypatch, [])
        conn = FakeConn({"ZZZZ":("ZZZZ", None)})
        assert company_info.enrich(conn, "ZZZZ") is False

class TestCaching:
    def test_a_profile_is_fetched_once_per_process(self, monkeypatch):
        monkeypatch.setattr(config, "FMP_ENABLED", True)
        calls = []
        class _Resp:
            status_code = 200
            def json(self):
                return [{"companyName":"Apple Inc.", "sector":"Technology"}]
            
        def _get(*a, **k):
            calls.append(1)
            return _Resp()
        monkeypatch.setattr(company_info.requests, "get", _get)
        company_info.fetch_profile("AAPL")
        company_info.fetch_profile("AAPL")
        company_info.fetch_profile("AAPL")
        assert len(calls) == 1

    def test_a_miss_is_cached_too_so_it_is_not_re_fetched(self, monkeypatch):
        monkeypatch.setattr(config, "FMP_ENABLED", True)
        calls = []
        class _Resp:
            status_code = 200
            def json(self):
                return []

        def _get(*a, **k):
            calls.append(1)
            return _Resp()
        monkeypatch.setattr(company_info.requests, "get", _get)
        assert company_info.fetch_profile("ZZZZ") is None
        assert company_info.fetch_profile("ZZZZ") is None
        assert len(calls) == 1

class TestBackfillAll:
    def test_enriches_only_the_placeholder_rows(self, no_fmp):
        conn = FakeConn({"AAPL":("AAPL", None), "MSFT":("MSFT", None), "TSLA":("Tesla, Inc.", "Consumer Cyclical"), "ZZZZ": ("ZZZZ", None)})
        result = company_info.backfill_all(conn)
        assert result == {"examined":3, "updated":2}
        assert conn.db["AAPL"]["sector"] == "Technology"
        assert conn.db["MSFT"]["sector"] == "Technology"
        assert conn.db["ZZZZ"]["sector"] is None
        assert conn.db["TSLA"]["name"] == "Tesla, Inc."

    def test_commits_once_when_anything_changed(self, no_fmp):
        conn = FakeConn({"AAPL":("AAPL", None)})
        company_info.backfill_all(conn)
        assert conn.committed == 1

    def test_does_not_commit_when_nothing_changed(self, no_fmp):
        conn = FakeConn({"TSLA":("Tesla, Inc.", "Consumer Cyclical")})
        company_info.backfill_all(conn)
        assert conn.committed == 0

    def test_an_empty_table_is_not_an_error(self, no_fmp):
        assert company_info.backfill_all(FakeConn({})) == {"examined":0, "updated":0}