"""
Tests for wallet.py — cash movement, weighted-average cost basis,
realized P&L, and portfolio valuation.

These used to be skipped on the grounds that buy_shares/sell_shares "need
a database". They don't need a *real* one: what they need is something
that answers `SELECT ... FOR UPDATE` and records what was written. So
these drive the real functions through a fake connection, which covers
more than extracting the arithmetic into pure helpers would — the
rounding tolerances, the rollback-on-failure path, the delete-at-zero
branch and the wallet-then-holding lock order are all part of the
behaviour worth pinning down, and none of them live in the arithmetic.

Routing in the fake is by distinguishing fragment of each statement
rather than exact SQL text, so reformatting a query in wallet.py doesn't
fail these tests for no reason.
"""
from datetime import date

import pytest

from backend.wallet import (
    InsufficientFundsError,
    InsufficientSharesError,
    buy_shares,
    ensure_wallet,
    get_cash_balance,
    get_holding,
    get_portfolio,
    save_daily_snapshot,
    sell_shares,
)

USER = 1


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._db = conn.db
        self._rows: list = []

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self._conn.statements.append(sql)
        holdings = self._db["holdings"]

        # Every SELECT branch is anchored with startswith. Matching on the
        # bare substring instead let "DELETE FROM holdings WHERE user_id"
        # fall into the holdings *lookup* branch, so the close-out never
        # reached the delete and two tests failed against correct code.
        if sql.startswith("SELECT") and "FROM wallets WHERE user_id" in sql:
            if "FOR UPDATE" in sql:
                self._conn.locks.append("wallet")
            cash = self._db.get("cash")
            self._rows = [(cash,)] if cash is not None else []

        elif sql.startswith("UPDATE wallets SET cash_balance"):
            self._db["cash"] = params[0]
            self._rows = []

        elif sql.startswith("INSERT INTO wallets"):
            self._db.setdefault("cash", params[1])
            self._rows = []

        # get_portfolio's join, before the plain holdings lookup below.
        elif sql.startswith("SELECT") and "FROM holdings h LEFT JOIN companies" in sql:
            self._rows = [
                (ticker, h["shares"], h["avg_cost_basis"], self._db["sectors"].get(ticker))
                for ticker, h in sorted(holdings.items())
            ]

        elif sql.startswith("SELECT") and "FROM holdings WHERE user_id" in sql:
            if "FOR UPDATE" in sql:
                self._conn.locks.append("holding")
            self._rows = self._holding_row(params)

        elif sql.startswith("DELETE FROM holdings"):
            holdings.pop(params[1], None)
            self._rows = []

        elif sql.startswith("INSERT INTO holdings"):
            _, ticker, shares, price = params
            holdings[ticker] = {"shares": shares, "avg_cost_basis": price}
            self._rows = []

        # Buy sets both columns; sell sets only shares. That difference is
        # the point of the "cost basis is not recalculated on a sell" rule,
        # so the fake distinguishes them rather than treating both alike.
        elif sql.startswith("UPDATE holdings SET shares") and "avg_cost_basis" in sql:
            shares, avg_cost, _, ticker = params
            holdings[ticker] = {"shares": shares, "avg_cost_basis": avg_cost}
            self._rows = []

        elif sql.startswith("UPDATE holdings SET shares"):
            shares, _, ticker = params
            holdings[ticker]["shares"] = shares
            self._rows = []

        elif sql.startswith("INSERT INTO transactions"):
            self._db["transactions"].append(params)
            self._rows = [(len(self._db["transactions"]),)]

        elif sql.startswith("INSERT INTO portfolio_snapshots"):
            self._db["snapshots"].append(params)
            self._rows = []

        else:
            raise AssertionError(f"Unexpected query: {sql}")

    def _holding_row(self, params):
        held = self._db["holdings"].get(params[1])
        return [(held["shares"], held["avg_cost_basis"])] if held else []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class FakeConn:
    def __init__(self, cash=100_000.0, holdings=None, sectors=None):
        self.db = {
            "cash": cash,
            "holdings": dict(holdings or {}),
            "sectors": dict(sectors or {}),
            "transactions": [],
            "snapshots": [],
        }
        self.statements: list[str] = []
        self.locks: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    # Convenience accessors used by the assertions below.
    @property
    def cash(self):
        return self.db["cash"]

    @property
    def holdings(self):
        return self.db["holdings"]

    @property
    def transactions(self):
        return self.db["transactions"]


def holding(shares, avg_cost):
    return {"shares": shares, "avg_cost_basis": avg_cost}


class TestBuyShares:
    def test_opens_a_new_position_at_the_purchase_price(self):
        conn = FakeConn(cash=10_000.0)

        result = buy_shares(conn, USER, "AAPL", 10, 150.0)

        assert conn.cash == pytest.approx(8_500.0)
        assert conn.holdings["AAPL"] == holding(10, 150.0)
        assert result["new_avg_cost_basis"] == 150.0
        assert result["new_shares"] == 10
        assert result["realized_pl"] is None
        assert conn.commits == 1

    def test_averages_cost_basis_across_two_purchases(self):
        # 10 @ $100 then 30 @ $200 -> (1000 + 6000) / 40 = $175
        conn = FakeConn(cash=100_000.0, holdings={"AAPL": holding(10.0, 100.0)})

        result = buy_shares(conn, USER, "AAPL", 30, 200.0)

        assert result["new_shares"] == pytest.approx(40.0)
        assert result["new_avg_cost_basis"] == pytest.approx(175.0)
        assert conn.holdings["AAPL"]["avg_cost_basis"] == pytest.approx(175.0)

    def test_averaging_is_share_weighted_not_a_midpoint(self):
        # A plain average of $100 and $200 would be $150. The correct
        # weighted figure is much closer to $200 because far more shares
        # were bought there — this is the case a midpoint bug passes and
        # the test above (equal-ish weights) would not catch as clearly.
        conn = FakeConn(cash=1_000_000.0, holdings={"AAPL": holding(1.0, 100.0)})

        result = buy_shares(conn, USER, "AAPL", 99, 200.0)

        assert result["new_avg_cost_basis"] == pytest.approx(199.0)

    def test_rejects_a_purchase_the_user_cannot_afford(self):
        conn = FakeConn(cash=1_000.0)

        with pytest.raises(InsufficientFundsError):
            buy_shares(conn, USER, "AAPL", 10, 150.0)

        assert conn.cash == 1_000.0
        assert conn.holdings == {}
        assert conn.transactions == []
        assert conn.commits == 0
        assert conn.rollbacks == 1

    def test_allows_a_purchase_that_spends_the_balance_exactly(self):
        # 3 * 33333.333333333336 is 100000.00000000001 in float, which a
        # naive `cost > cash` comparison rejects even though the user can
        # afford it to the cent. The rounding in buy_shares exists for this.
        conn = FakeConn(cash=100_000.0)

        buy_shares(conn, USER, "AAPL", 3, 100_000 / 3)

        assert conn.cash == pytest.approx(0.0, abs=1e-6)
        assert conn.commits == 1

    def test_locks_the_wallet_before_the_holding(self):
        # buy and sell must take these two locks in the same order or two
        # concurrent trades on one account can deadlock against each other.
        conn = FakeConn(cash=10_000.0)

        buy_shares(conn, USER, "AAPL", 1, 100.0)

        assert conn.locks == ["wallet", "holding"]

    def test_records_the_transaction_with_the_prediction_flag(self):
        conn = FakeConn(cash=10_000.0)

        buy_shares(conn, USER, "AAPL", 2, 50.0, triggered_by_prediction=True)

        user_id, ticker, shares, price, triggered = conn.transactions[0]
        assert (user_id, ticker, shares, price, triggered) == (USER, "AAPL", 2, 50.0, True)

    @pytest.mark.parametrize("shares,price", [(0, 100.0), (-5, 100.0), (10, 0), (10, -1.0)])
    def test_rejects_non_positive_share_counts_and_prices(self, shares, price):
        conn = FakeConn(cash=10_000.0)

        with pytest.raises(ValueError):
            buy_shares(conn, USER, "AAPL", shares, price)

        # Rejected before any lock is taken, so nothing to roll back.
        assert conn.statements == []


class TestSellShares:
    def test_realizes_profit_and_leaves_cost_basis_alone(self):
        conn = FakeConn(cash=0.0, holdings={"AAPL": holding(10.0, 100.0)})

        result = sell_shares(conn, USER, "AAPL", 4, 150.0)

        assert result["realized_pl"] == pytest.approx(200.0)   # 4 * (150 - 100)
        assert conn.cash == pytest.approx(600.0)               # 4 * 150 proceeds
        assert result["remaining_shares"] == pytest.approx(6.0)
        # Selling draws down share count only — averaging is a buy-side rule.
        assert conn.holdings["AAPL"]["avg_cost_basis"] == 100.0
        assert conn.commits == 1

    def test_realizes_a_loss_as_a_negative_number(self):
        conn = FakeConn(cash=0.0, holdings={"AAPL": holding(10.0, 100.0)})

        result = sell_shares(conn, USER, "AAPL", 10, 75.0)

        assert result["realized_pl"] == pytest.approx(-250.0)

    def test_closing_the_position_deletes_the_holding(self):
        conn = FakeConn(cash=0.0, holdings={"AAPL": holding(5.0, 20.0)})

        result = sell_shares(conn, USER, "AAPL", 5, 25.0)

        assert "AAPL" not in conn.holdings
        assert result["remaining_shares"] == 0.0

    def test_a_float_dust_remainder_closes_the_position_rather_than_stranding_it(self):
        # 0.1 + 0.2 owned, sell 0.3: the remainder is ~5.5e-17, not zero.
        # Left in place that becomes a holding the user can see but never
        # sell, so it must round to closed.
        conn = FakeConn(cash=0.0, holdings={"AAPL": holding(0.1 + 0.2, 10.0)})

        result = sell_shares(conn, USER, "AAPL", 0.3, 12.0)

        assert result["remaining_shares"] == 0.0
        assert "AAPL" not in conn.holdings

    def test_rejects_selling_more_than_is_owned(self):
        conn = FakeConn(cash=0.0, holdings={"AAPL": holding(5.0, 20.0)})

        with pytest.raises(InsufficientSharesError):
            sell_shares(conn, USER, "AAPL", 6, 25.0)

        assert conn.holdings["AAPL"]["shares"] == 5.0
        assert conn.cash == 0.0
        assert conn.commits == 0
        assert conn.rollbacks == 1

    def test_rejects_selling_a_ticker_that_is_not_held(self):
        conn = FakeConn(cash=0.0, holdings={})

        with pytest.raises(InsufficientSharesError) as exc:
            sell_shares(conn, USER, "MSFT", 1, 25.0)

        assert "only 0 owned" in str(exc.value)
        assert conn.rollbacks == 1

    def test_locks_the_wallet_before_the_holding(self):
        conn = FakeConn(cash=0.0, holdings={"AAPL": holding(5.0, 20.0)})

        sell_shares(conn, USER, "AAPL", 1, 25.0)

        assert conn.locks == ["wallet", "holding"]

    def test_records_realized_pl_on_the_transaction_row(self):
        conn = FakeConn(cash=0.0, holdings={"AAPL": holding(10.0, 100.0)})

        sell_shares(conn, USER, "AAPL", 2, 130.0)

        user_id, ticker, shares, price, realized, triggered = conn.transactions[0]
        assert (user_id, ticker, shares, price) == (USER, "AAPL", 2, 130.0)
        assert realized == pytest.approx(60.0)
        assert triggered is False

    @pytest.mark.parametrize("shares,price", [(0, 100.0), (-5, 100.0), (10, 0), (10, -1.0)])
    def test_rejects_non_positive_share_counts_and_prices(self, shares, price):
        conn = FakeConn(cash=0.0, holdings={"AAPL": holding(10.0, 100.0)})

        with pytest.raises(ValueError):
            sell_shares(conn, USER, "AAPL", shares, price)

        assert conn.statements == []


class TestRoundTrip:
    def test_buy_buy_sell_leaves_consistent_cash_and_basis(self):
        conn = FakeConn(cash=10_000.0)

        buy_shares(conn, USER, "AAPL", 10, 100.0)     # -1000 -> 9000
        buy_shares(conn, USER, "AAPL", 10, 140.0)     # -1400 -> 7600, basis 120
        result = sell_shares(conn, USER, "AAPL", 5, 200.0)  # +1000 -> 8600

        assert conn.holdings["AAPL"]["avg_cost_basis"] == pytest.approx(120.0)
        assert conn.holdings["AAPL"]["shares"] == pytest.approx(15.0)
        assert conn.cash == pytest.approx(8_600.0)
        # Realized on the sold 5 only: 5 * (200 - 120).
        assert result["realized_pl"] == pytest.approx(400.0)
        assert len(conn.transactions) == 3


class TestGetPortfolio:
    def test_values_holdings_and_totals_cash_plus_market_value(self):
        conn = FakeConn(
            cash=1_000.0,
            holdings={"AAPL": holding(10.0, 100.0), "MSFT": holding(5.0, 200.0)},
            sectors={"AAPL": "Technology", "MSFT": "Technology"},
        )

        portfolio = get_portfolio(conn, USER, {"AAPL": 120.0, "MSFT": 180.0})

        assert portfolio["holdings_value"] == pytest.approx(2_100.0)   # 1200 + 900
        assert portfolio["total_value"] == pytest.approx(3_100.0)
        assert portfolio["cash_balance"] == 1_000.0

        aapl, msft = portfolio["holdings"]
        assert aapl["unrealized_pl"] == pytest.approx(200.0)
        assert aapl["unrealized_pl_pct"] == pytest.approx(0.2)
        assert aapl["sector"] == "Technology"
        assert msft["unrealized_pl"] == pytest.approx(-100.0)

    def test_an_unpriced_holding_is_reported_without_dropping_the_rest(self):
        # The nightly snapshot depends on this: a ticker with no stored
        # close must not zero out or omit the user's whole portfolio.
        conn = FakeConn(
            cash=500.0,
            holdings={"AAPL": holding(10.0, 100.0), "OBSCURE": holding(3.0, 50.0)},
        )

        portfolio = get_portfolio(conn, USER, {"AAPL": 110.0})

        obscure = next(h for h in portfolio["holdings"] if h["ticker"] == "OBSCURE")
        assert obscure["market_value"] is None
        assert obscure["unrealized_pl"] is None
        assert obscure["unrealized_pl_pct"] is None
        # Cash plus only what could be priced.
        assert portfolio["holdings_value"] == pytest.approx(1_100.0)
        assert portfolio["total_value"] == pytest.approx(1_600.0)

    def test_a_zero_cost_basis_does_not_divide_by_zero(self):
        conn = FakeConn(cash=0.0, holdings={"FREE": holding(10.0, 0.0)})

        portfolio = get_portfolio(conn, USER, {"FREE": 5.0})

        assert portfolio["holdings"][0]["unrealized_pl"] == pytest.approx(50.0)
        assert portfolio["holdings"][0]["unrealized_pl_pct"] is None

    def test_an_empty_portfolio_is_just_cash(self):
        conn = FakeConn(cash=100_000.0, holdings={})

        portfolio = get_portfolio(conn, USER, {})

        assert portfolio["holdings"] == []
        assert portfolio["holdings_value"] == 0.0
        assert portfolio["total_value"] == 100_000.0


class TestWalletHelpers:
    def test_get_cash_balance_raises_when_no_wallet_exists(self):
        conn = FakeConn(cash=None)

        with pytest.raises(ValueError, match="No wallet"):
            get_cash_balance(conn, USER)

    def test_get_holding_returns_none_for_an_unheld_ticker(self):
        conn = FakeConn(holdings={"AAPL": holding(1.0, 10.0)})

        assert get_holding(conn, USER, "MSFT") is None
        assert get_holding(conn, USER, "AAPL") == {"shares": 1.0, "avg_cost_basis": 10.0}

    def test_ensure_wallet_does_not_overwrite_an_existing_balance(self):
        conn = FakeConn(cash=42.0)

        ensure_wallet(conn, USER)

        assert conn.cash == 42.0

    def test_save_daily_snapshot_writes_the_three_value_columns(self):
        conn = FakeConn(cash=1_000.0)
        portfolio = {"total_value": 3_100.0, "cash_balance": 1_000.0,
                     "holdings_value": 2_100.0}

        save_daily_snapshot(conn, USER, date(2024, 3, 14), portfolio)

        user_id, as_of, total, cash, holdings_value = conn.db["snapshots"][0]
        assert (user_id, as_of) == (USER, date(2024, 3, 14))
        assert (total, cash, holdings_value) == (3_100.0, 1_000.0, 2_100.0)
        assert conn.commits == 1
