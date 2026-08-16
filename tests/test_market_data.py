import pytest
from backend import market_data

class FakeResponse:
    def __init__(self, payload, status_code = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

class FakeCursor:
    def __init__(self, row):
        self._row = row

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return self._row

    def close(self):
        pass

class FakeConn:
    def __init__(self, row = None):
        self._row = row

    def cursor(self):
        return FakeCursor(self._row)

YAHOO_OK = {"chart":{"result":[{"meta":{"regularMarketPrice":190.25}}], "error":None}}
FMP_OK = [{"symbol":"AAPL", "price":191.5}]

@pytest.fixture(autouse=True)
def _clean_cache_and_config(monkeypatch):
    market_data.clear_quote_cache()
    monkeypatch.setattr(market_data.config, "LIVE_QUOTES_ENABLED", True)
    monkeypatch.setattr(market_data.config, "QUOTE_CACHE_TTL_SECONDS", 60)
    monkeypatch.setattr(market_data.config, "FMP_ENABLED", False)
    monkeypatch.setattr(market_data.config, "FMP_API_KEY", "")
    monkeypatch.setattr(market_data.alpaca_client, "is_configured", lambda:False)
    yield
    market_data.clear_quote_cache()

class TestNoAlpacaAccount:
    def test_yahoo_prices_a_ticker_with_no_keys_at_all(self, monkeypatch):
        monkeypatch.setattr(market_data.requests, "get", lambda *a, **k: FakeResponse(YAHOO_OK))
        quote = market_data.get_price(FakeConn(), "AAPL")
        assert quote["price"] == 190.25
        assert quote["source"] == "yahoo"

    def test_fmp_is_preferred_over_yahoo_when_a_key_exists(self, monkeypatch):
        monkeypatch.setattr(market_data.config, "FMP_ENABLED", True)
        monkeypatch.setattr(market_data.config, "FMP_API_KEY", "real-looking-key")

        def fake_get(url, **kwargs):
            assert "yahoo" not in url, "Yahoo should not be called once FMP succeeds"
            return FakeResponse(FMP_OK)
        monkeypatch.setattr(market_data.requests, "get", fake_get)
        assert market_data.get_price(FakeConn(), "AAPL")["source"] == "fmp"

    def test_falls_through_to_yahoo_when_fmp_errors(self, monkeypatch):
        monkeypatch.setattr(market_data.config, "FMP_ENABLED", True)
        monkeypatch.setattr(market_data.config, "FMP_API_KEY", "expired-key")

        def fake_get(url, **kwargs):
            if "financialmodelingprep" in url:
                return FakeResponse({"Error Message": "Invalid API KEY"})
            return FakeResponse(YAHOO_OK)
        monkeypatch.setattr(market_data.requests, "get", fake_get)
        assert market_data.get_price(FakeConn(), "AAPL")["source"] == "yahoo"

    def test_stored_close_is_used_when_every_network_source_is_down(self, monkeypatch):

        def dead_network(*args, **kwargs):
            raise ConnectionError("no route to host")
        monkeypatch.setattr(market_data.requests, "get", dead_network)
        quote = market_data.get_price(FakeConn(row = (187.0, "2026-07-31")), "AAPL")
        assert quote == {"price":187.0, "source":"price_history", "as_of":"2026-07-31"}

    def test_raises_only_when_nothing_at_all_can_price_it(self, monkeypatch):
        monkeypatch.setattr(market_data.requests, "get", lambda *a, **k:(_ for _ in ()).throw(ConnectionError()))
        with pytest.raises(market_data.PriceUnavailable):
            market_data.get_price(FakeConn(row = None), "NOSUCHTICKER")

    def test_error_message_does_not_demand_a_brokerage_account(self, monkeypatch):
        monkeypatch.setattr(market_data.requests, "get", lambda *a, **k:(_ for _ in ()).throw(ConnectionError()))
        with pytest.raises(market_data.PriceUnavailable) as excinfo:
            market_data.get_price(FakeConn(row = None), "NOSUCHTICKER")
        assert "alpaca" not in str(excinfo.value).lower()
        assert "fetch_prices.py" in str(excinfo.value)

class TestOfflineMode:
    def test_live_quotes_disabled_skips_the_network_entirely(self, monkeypatch):
        monkeypatch.setattr(market_data.config, "LIVE_QUOTES_ENABLED", False)

        def fail_if_called(*args, **kwargs):
            raise AssertionError("LIVE_QUOTES=0 must not make network calls")
        monkeypatch.setattr(market_data.requests, "get", fail_if_called)
        quote = market_data.get_price(FakeConn(row = (150.0, "2026-07-31")), "AAPL")
        assert quote["source"] == "price_history"

class TestQuoteCache:
    def test_second_lookup_is_served_from_cache(self, monkeypatch):
        calls = []

        def counting_get(*args, **kwargs):
            calls.append(1)
            return FakeResponse(YAHOO_OK)
        monkeypatch.setattr(market_data.requests, "get", counting_get)
        market_data.get_price(FakeConn(), "AAPL")
        market_data.get_price(FakeConn(), "AAPL")
        assert len(calls) == 1

    def test_expired_entry_is_refetched(self, monkeypatch):
        calls = []

        def counting_get(*args, **kwargs):
            calls.append(1)
            return FakeResponse(YAHOO_OK)
        monkeypatch.setattr(market_data.requests, "get", counting_get)
        monkeypatch.setattr(market_data.config, "QUOTE_CACHE_TTL_SECONDS", 60)
        market_data.get_price(FakeConn(), "AAPL")
        real_monotonic = market_data.time.monotonic
        monkeypatch.setattr(market_data.time, "monotonic", lambda: real_monotonic() + 3600)
        market_data.get_price(FakeConn(), "AAPL")
        assert len(calls) == 2

    def test_ttl_of_zero_disables_caching(self, monkeypatch):
        calls = []
        monkeypatch.setattr(market_data.config, "QUOTE_CACHE_TTL_SECONDS", 0)
        monkeypatch.setattr(market_data.requests, "get", lambda *a, **k:(calls.append(1), FakeResponse(YAHOO_OK))[1])
        market_data.get_price(FakeConn(), "AAPL")
        market_data.get_price(FakeConn(), "AAPL")
        assert len(calls) == 2

class TestBulkLookup:
    def test_prices_several_tickers(self, monkeypatch):
        monkeypatch.setattr(market_data.requests, "get", lambda *a, **k:FakeResponse(YAHOO_OK))
        quotes = market_data.get_prices(FakeConn(), ["AAPL", "MSFT", "NVDA"])
        assert set(quotes) == {"AAPL", "MSFT", "NVDA"}
        assert all(q["source"] == "yahoo" for q in quotes.values())

    def test_duplicate_tickers_are_looked_up_once(self, monkeypatch):
        calls = []
        monkeypatch.setattr(market_data.requests, "get", lambda *a, **k:(calls.append(1), FakeResponse(YAHOO_OK))[1])
        quotes = market_data.get_prices(FakeConn(), ["AAPL", "AAPL", "AAPL"])
        assert set(quotes) == {"AAPL"}
        assert len(calls) == 1

    def test_empty_list_short_circuits(self, monkeypatch):
        def fail_if_called(*args, **kwargs):
            raise AssertionError("no tickers means no calls")
        monkeypatch.setattr(market_data.requests, "get", fail_if_called)
        assert market_data.get_prices(FakeConn(), []) == {}

    def test_unpriceable_ticker_is_omitted_not_fatal(self, monkeypatch):
        def fake_get(url, **kwargs):
            if "MSFT" in url:
                raise ConnectionError("upstream refused")
            return FakeResponse(YAHOO_OK)
        monkeypatch.setattr(market_data.requests, "get", fake_get)
        quotes = market_data.get_prices(FakeConn(row = None), ["AAPL", "MSFT"])
        assert "AAPL" in quotes
        assert "MSFT" not in quotes