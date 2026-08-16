# trading_models.py — Defines ML models used for predictions.
pass
from pydantic import BaseModel, Field

class TradeRequest(BaseModel):
    ticker: str = Field(min_length = 1, max_length = 10)
    shares: float = Field(gt = 0, le = 1_000_000)
    triggered_by_prediction: bool = False
    explain: bool = True

class HoldingResponse(BaseModel):
    ticker: str
    shares: float
    avg_cost_basis: float
    sector: str | None = None
    current_price: float | None
    market_value: float | None
    unrealized_pl: float | None
    unrealized_pl_pct: float | None = None

class PortfolioResponse(BaseModel):
    cash_balance: float
    holdings: list[HoldingResponse]
    holdings_value: float
    total_value: float

class TradeResponse(BaseModel):
    transaction_id: int
    executed_price: float
    price_source: str
    new_cash_balance: float
    realized_pl: float | None = None
    new_shares: float | None = None
    new_avg_cost_basis: float | None = None
    remaining_shares: float | None = None
    explanation: str | None = None

class SnapshotResponse(BaseModel):
    date: str
    total_value: float
    cash_value: float
    holdings_value: float